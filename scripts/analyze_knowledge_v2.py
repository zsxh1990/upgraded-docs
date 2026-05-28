#!/usr/bin/env python3
"""
知识图谱 LLM 分析 V3 — 分节提取 + 丰富知识类型 + 跨文档关系

改进点（相比 V2）：
1. 分节提取 — 按文档章节拆分，消除 2000 字符硬截断
2. 丰富知识类型 — 新增 process/configuration 实体、safety/constraint/process_step 声明
3. 实体间关系 — 文档内实体之间的 uses/configures/requires 关系
4. 跨文档关系 — references/depends_on/shares_entity/extends
5. max_tokens 6000 — 充分利用模型输出能力
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import requests

# 强制 stdout 行缓冲（兼容管道重定向）
sys.stdout.reconfigure(line_buffering=True)

BASE = Path(__file__).resolve().parent.parent
MANIFEST_PATH = BASE / "wiki" / ".understand-anything" / "intermediate" / "scan-manifest.json"
OUTPUT_DIR = BASE / "wiki" / ".understand-anything" / "intermediate"

# LLM 后端: LM Studio 本地 (设置 USE_MIFY=1 启用 Mify 网关回退)
LM_STUDIO_URL = os.environ.get("LM_STUDIO_URL", "http://localhost:1234/v1/chat/completions")
LM_STUDIO_MODEL = os.environ.get("LM_STUDIO_MODEL", "mlx-qwen3.5-9b-opus-distilled-v2")
USE_MIFY = os.environ.get("USE_MIFY", "") == "1"
MIFY_API_KEY = os.environ.get("MIFY_API_KEY", "")
MIFY_BASE_URL = os.environ.get("MIFY_BASE_URL", "http://model.mify.ai.srv")
MIFY_MODEL = os.environ.get("MIFY_MODEL", "Qwen3-235B-A22B")

# ──────────────────────────── Prompts ────────────────────────────

SYSTEM_PROMPT = """你是小米汽车车身工程领域的知识图谱提取专家。分析技术文档章节，提取四类知识：

## 领域背景知识
- **Tecnomatix** = 西门子工业软件，含 Process Designer (PD) 和 Process Simulate (PS)。"Tecnomatix""PD/PS""西门子PD/PS"是同义词。
- **仿真评审**分阶段：第一次评审、第二次评审、预验收、终验收。不同阶段要求不同是正常递进，不是矛盾。
- **Plant Simulation** 是 Tecnomatix 的物流仿真工具，与 PD/PS 互补。

## 提取规则

### 1. 实体(Entities)
提取具体工具、设备、软件、标准、命名流程、配置集。类型：
- tool: 具体工具（如 "TCP校准针"、"零点标定块"）
- device: 具体设备型号（如 "FANUC R-2000iC"、"R30iB"）
- software: 软件产品（如 "Tecnomatix"、"RobotStudio"）
- standard: 标准规范（如 "GB/T 15706-2012"）
- file_format: 文件格式（如 ".ls文件"、"JBI格式"）
- process: 命名流程/程序（如 "MI_FAP"、"SpotTool+"、"Segment"）
- configuration: 配置参数集（如 "Profinet配置"、"I/O映射表"）

要求：名称必须具体，不能是泛称（"机器人"太泛，应为"FANUC R-2000iC"）。

### 2. 声明(Claims)
提取技术要求、参数、规则、流程步骤。类别：
- parameter: 具体参数值（如 "精度±0.05mm"）
- threshold: 阈值/判定标准（如 "碰撞安全距离≥5mm"）
- format: 格式要求（如 "命名规则: 设备号_工位号_序号"）
- safety: 安全要求（如 "仅允许受训人员操作"、"急停后需手动复位"）
- constraint: 规则/禁令（如 "勿发出圆弧运动指令"、"不允许使用绝对坐标"）
- process_step: 流程步骤（如 "1.回零点 2.标定TCP 3.验证精度"）

要求：必须包含明确条件或具体数值，不要提取泛泛描述。

### 3. 文档内关系(Relationships)
提取实体之间的关系（同一文档内的）：
- uses: A 使用 B（如 "FANUC R30iB 使用 Profinet协议"）
- configures: A 配置 B（如 "I/O映射表 配置 PLC通信"）
- requires: A 要求 B（如 "碰撞检测 需要 安全区域定义"）
- produces: A 产出 B（如 "离线编程 产出 .ls文件"）

### 4. 矛盾(Contradictions)
**严格标准**：同一参数在本章节内出现自相矛盾的数值才算。
一般单章节内不会出现矛盾，返回空数组即可。

## 输出格式（严格JSON）
```json
{
  "entities": [
    {"name": "具体名称", "type": "tool/device/software/standard/file_format/process/configuration", "summary": "一句话描述", "source_doc": "文档标题"}
  ],
  "claims": [
    {"title": "要求/参数名称", "content": "具体条件或数值", "category": "parameter/threshold/format/safety/constraint/process_step", "source_doc": "文档标题"}
  ],
  "relationships": [
    {"from": "实体A名称", "to": "实体B名称", "relation": "uses/configures/requires/produces", "evidence": "原文依据", "source_doc": "文档标题"}
  ],
  "contradictions": []
}
```"""

SECTION_PROMPT_TEMPLATE = """分析以下技术文档章节，提取知识：

文档: {doc_name}
分类: {category}
章节: {section_heading}

内容:
{section_content}

以JSON格式输出。记住：
- 实体名称要具体（设备型号、软件名、标准号）
- 声明要有明确条件或数值
- 关系要有原文依据
- contradictions 返回空数组"""

CROSS_DOC_PROMPT = """你是小米汽车车身工程领域专家。分析以下文档摘要，找出文档之间的关系。

## 文档摘要
{summaries}

## 关系类型
- references: 文档A引用了文档B中的内容或标准
- depends_on: 使用文档A的设备/流程需要遵循文档B的规范
- shares_entity: 两篇文档涉及同一实体（设备/软件/标准），至少共享2个实体
- extends: 文档A是文档B的扩展或细化版本

## 输出格式（严格JSON）
```json
{{
  "relationships": [
    {{"from": "文档A标题", "to": "文档B标题", "type": "references/depends_on/shares_entity/extends", "evidence": "具体依据", "shared_entities": ["共享实体名"]}}
  ]
}}
```

如果没有找到关系，返回空 relationships 数组。"""


# ──────────────────────────── Section Splitting ────────────────────────────

def split_into_sections(content: str, max_chars: int = 3000, min_chars: int = 800) -> list[dict]:
    """按标题将文档拆分为章节，合并小节。

    返回 [{"heading": "章节标题", "content": "章节内容"}, ...]
    短文档（<4000 chars）返回单个章节。
    """
    if len(content) < 4000:
        return [{"heading": "全文", "content": content}]

    # 只匹配 ## / ### 标题和主要编号标题（如 "1. " "4.1 "），跳过 h1 和子列表
    heading_pattern = re.compile(r'^(#{2,3}\s+.+|\d+\.\s+[^\d].*)', re.MULTILINE)

    headings_found = list(heading_pattern.finditer(content))

    if not headings_found:
        # 没有标题，按字符数切分
        chunks = []
        for i in range(0, len(content), max_chars):
            chunks.append({"heading": f"片段{i//max_chars + 1}", "content": content[i:i + max_chars]})
        return chunks

    # 按标题分节（包含第一个标题前的内容）
    raw_sections = []
    first_heading_pos = headings_found[0].start()
    if first_heading_pos > 100:
        # 第一个标题前有实质内容，单独成节
        raw_sections.append({"heading": "前言", "content": content[:first_heading_pos].strip()})

    for i, m in enumerate(headings_found):
        pos = m.start()
        end = headings_found[i + 1].start() if i + 1 < len(headings_found) else len(content)
        section_content = content[pos:end].strip()
        if section_content:
            raw_sections.append({"heading": m.group().strip(), "content": section_content})

    # 合并小节：连续小节合并直到达到 min_chars
    merged = []
    buf_heading = ""
    buf_content = ""

    for sec in raw_sections:
        if buf_content and len(buf_content) + len(sec["content"]) > max_chars:
            # 缓冲区加上新节会超限，先保存缓冲区
            merged.append({"heading": buf_heading, "content": buf_content})
            buf_heading = sec["heading"]
            buf_content = sec["content"]
        elif buf_content and len(buf_content) >= min_chars:
            # 缓冲区已经够大，保存并开始新缓冲
            merged.append({"heading": buf_heading, "content": buf_content})
            buf_heading = sec["heading"]
            buf_content = sec["content"]
        else:
            # 合并到缓冲区
            if not buf_heading:
                buf_heading = sec["heading"]
            buf_content += "\n\n" + sec["content"] if buf_content else sec["content"]

    if buf_content:
        merged.append({"heading": buf_heading, "content": buf_content})

    # 截断过长的节
    result = []
    for sec in merged:
        if len(sec["content"]) > max_chars:
            result.append({"heading": sec["heading"], "content": sec["content"][:max_chars] + "\n...(截断)"})
        else:
            result.append(sec)

    return result if result else [{"heading": "全文", "content": content[:max_chars]}]


# ──────────────────────────── LLM Calls ────────────────────────────

def _try_llm(url: str, model: str, api_key: str, messages: list[dict]) -> str:
    """尝试调用单个 LLM 端点。"""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = requests.post(
        url,
        headers=headers,
        json={
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 6000,
        },
        timeout=300,
    )
    resp.raise_for_status()
    msg = resp.json()["choices"][0]["message"]
    return msg.get("content") or msg.get("reasoning_content") or ""


def call_llm(messages: list[dict]) -> str:
    """调用 LLM API。默认 LM Studio，设 USE_MIFY=1 启用 Mify 回退。"""
    # 尝试 LM Studio（主要）
    try:
        return _try_llm(LM_STUDIO_URL, LM_STUDIO_MODEL, "", messages)
    except Exception as e:
        if not USE_MIFY:
            print(f"  ⚠️ LM Studio 调用失败: {e}", file=sys.stderr)
            return ""

    # 回退 Mify 网关
    if USE_MIFY and MIFY_API_KEY:
        try:
            url = f"{MIFY_BASE_URL}/v1/chat/completions"
            return _try_llm(url, MIFY_MODEL, MIFY_API_KEY, messages)
        except Exception as e:
            print(f"  ⚠️ Mify 调用失败: {e}", file=sys.stderr)

    return ""


def parse_json_response(text: str) -> dict:
    """从 LLM 响应中提取 JSON。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r'```json\s*\n(.*?)\n\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return {"entities": [], "claims": [], "relationships": [], "contradictions": []}


# ──────────────────────────── Deduplication ────────────────────────────

def normalize_name(name: str) -> str:
    return name.lower().strip().replace(" ", "-")


def merge_section_results(section_results: list[dict], doc_name: str) -> dict:
    """合并同一文档多个章节的提取结果，去重。"""
    entity_names = set()
    claim_titles = set()
    rel_keys = set()

    merged = {"entities": [], "claims": [], "relationships": [], "contradictions": []}

    for r in section_results:
        for e in r.get("entities", []):
            norm = normalize_name(e.get("name", ""))
            if norm and norm not in entity_names:
                entity_names.add(norm)
                e["source_doc"] = doc_name
                merged["entities"].append(e)

        for c in r.get("claims", []):
            norm = normalize_name(c.get("title", ""))
            if norm and norm not in claim_titles:
                claim_titles.add(norm)
                c["source_doc"] = doc_name
                merged["claims"].append(c)

        for rel in r.get("relationships", []):
            key = (normalize_name(rel.get("from", "")), normalize_name(rel.get("to", "")), rel.get("relation", ""))
            if key not in rel_keys:
                rel_keys.add(key)
                rel["source_doc"] = doc_name
                merged["relationships"].append(rel)

        merged["contradictions"].extend(r.get("contradictions", []))

    return merged


def deduplicate_contradictions(contradictions: list[dict]) -> list[dict]:
    """去重并过滤假阳性矛盾。"""
    seen = set()
    filtered = []

    for c in contradictions:
        key = tuple(sorted([c.get("doc1", ""), c.get("doc2", "")])) + (c.get("param", ""),)
        if key in seen:
            continue
        seen.add(key)

        v1 = c.get("value1", "").strip()
        v2 = c.get("value2", "").strip()

        none_indicators = ["未定义", "未明确", "未包含", "未说明", "未提及", "未规定"]
        is_none_v1 = any(ind in v1 for ind in none_indicators)
        is_none_v2 = any(ind in v2 for ind in none_indicators)

        if is_none_v1 and is_none_v2:
            continue
        elif is_none_v1 or is_none_v2:
            c["confidence"] = "low"
            c["note"] = "一方未提及，可能是覆盖范围差异而非矛盾"

        filtered.append(c)

    return filtered


# ──────────────────────────── Cross-Document Relationships ────────────────────────────

def build_doc_summaries(doc_results: list[dict]) -> list[dict]:
    """为每篇文档生成紧凑摘要。"""
    summaries = []
    for dr in doc_results:
        entity_names = [e.get("name", "") for e in dr.get("entities", [])]
        claim_summaries = [c.get("title", "") for c in dr.get("claims", [])[:5]]
        summaries.append({
            "name": dr.get("doc_name", ""),
            "category": dr.get("category", ""),
            "entities": entity_names,
            "key_claims": claim_summaries,
        })
    return summaries


def extract_cross_doc_relations(summaries: list[dict]) -> list[dict]:
    """用 LLM 提取文档间关系。"""
    # 格式化摘要
    text_parts = []
    for i, s in enumerate(summaries):
        entities_str = ", ".join(s["entities"][:10]) if s["entities"] else "无"
        claims_str = ", ".join(s["key_claims"]) if s["key_claims"] else "无"
        text_parts.append(f"{i+1}. **{s['name']}** [{s['category']}]\n   实体: {entities_str}\n   关键参数: {claims_str}")

    summaries_text = "\n".join(text_parts)
    prompt = CROSS_DOC_PROMPT.format(summaries=summaries_text)

    messages = [
        {"role": "system", "content": "你是文档关系分析专家。"},
        {"role": "user", "content": prompt},
    ]

    response = call_llm(messages)
    if not response:
        return []

    result = parse_json_response(response)
    return result.get("relationships", [])


# ──────────────────────────── Main Pipeline ────────────────────────────

def main():
    if not MANIFEST_PATH.exists():
        print("❌ scan-manifest.json 不存在", file=sys.stderr)
        sys.exit(1)

    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    articles = [n for n in manifest["nodes"] if n["type"] == "article"]
    print(f"📚 共 {len(articles)} 篇文章待分析")

    # ── Phase 1: Per-document, per-section extraction ──
    all_doc_results = []
    total_sections = 0

    for doc_idx, doc in enumerate(articles):
        doc_name = doc["name"]
        content = doc.get("knowledgeMeta", {}).get("content", "")
        category = doc.get("knowledgeMeta", {}).get("category", "无")

        sections = split_into_sections(content)
        total_sections += len(sections)
        print(f"\n📄 [{doc_idx+1}/{len(articles)}] {doc_name} — {len(content)} chars → {len(sections)} 节")

        section_results = []
        for sec_idx, sec in enumerate(sections):
            user_prompt = SECTION_PROMPT_TEMPLATE.format(
                doc_name=doc_name,
                category=category,
                section_heading=sec["heading"],
                section_content=sec["content"],
            )

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            response = call_llm(messages)
            if not response:
                print(f"  ⚠️ 节 {sec_idx+1}/{len(sections)} ({sec['heading'][:20]}) 失败")
                continue

            result = parse_json_response(response)
            section_results.append(result)

            e_cnt = len(result.get("entities", []))
            c_cnt = len(result.get("claims", []))
            r_cnt = len(result.get("relationships", []))
            print(f"  ✅ 节 {sec_idx+1}/{len(sections)} ({sec['heading'][:25]}): {e_cnt}实体 {c_cnt}声明 {r_cnt}关系")

            time.sleep(0.3)

        # Merge sections for this document
        merged = merge_section_results(section_results, doc_name)
        merged["doc_name"] = doc_name
        merged["category"] = category
        all_doc_results.append(merged)

        m_e = len(merged["entities"])
        m_c = len(merged["claims"])
        m_r = len(merged["relationships"])
        print(f"  📊 合并: {m_e}实体, {m_c}声明, {m_r}关系")

    # ── Phase 2: Cross-document relationship extraction ──
    print(f"\n{'='*60}")
    print(f"🔗 Phase 2: 跨文档关系提取...")
    summaries = build_doc_summaries(all_doc_results)

    # Batch summaries (10 per batch)
    cross_doc_relations = []
    batch_size = 10
    for i in range(0, len(summaries), batch_size):
        batch = summaries[i:i + batch_size]
        print(f"  📦 批次 {i//batch_size + 1} ({len(batch)} 篇)...")
        relations = extract_cross_doc_relations(batch)
        cross_doc_relations.extend(relations)
        print(f"  ✅ 发现 {len(relations)} 条跨文档关系")
        time.sleep(0.5)

    # ── Aggregate results ──
    all_entities = []
    all_claims = []
    all_relationships = []
    all_contradictions = []

    for dr in all_doc_results:
        all_entities.extend(dr.get("entities", []))
        all_claims.extend(dr.get("claims", []))
        all_relationships.extend(dr.get("relationships", []))
        all_contradictions.extend(dr.get("contradictions", []))

    all_relationships.extend(cross_doc_relations)
    all_contradictions = deduplicate_contradictions(all_contradictions)

    # ── Output ──
    output = {
        "entities": all_entities,
        "claims": all_claims,
        "relationships": all_relationships,
        "contradictions": all_contradictions,
        "cross_doc_relations": cross_doc_relations,
        "stats": {
            "total_articles": len(articles),
            "total_sections": total_sections,
            "total_entities": len(all_entities),
            "total_claims": len(all_claims),
            "total_relationships": len(all_relationships),
            "total_cross_doc_relations": len(cross_doc_relations),
            "total_contradictions": len(all_contradictions),
        },
    }

    output_path = OUTPUT_DIR / "llm-analysis-v2.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"📊 分析完成!")
    print(f"  文档: {len(articles)} 篇, {total_sections} 节")
    print(f"  实体: {len(all_entities)} (avg {len(all_entities)/max(len(articles),1):.1f}/doc)")
    print(f"  声明: {len(all_claims)} (avg {len(all_claims)/max(len(articles),1):.1f}/doc)")
    print(f"  文档内关系: {len(all_relationships) - len(cross_doc_relations)}")
    print(f"  跨文档关系: {len(cross_doc_relations)}")
    print(f"  矛盾: {len(all_contradictions)}")
    print(f"📁 结果保存: {output_path}")


if __name__ == "__main__":
    main()
