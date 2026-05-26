#!/usr/bin/env python3
"""
知识图谱 LLM 分析 V2 — 改进版

改进点：
1. 明确区分"矛盾"和"未提及" — 避免假阳性
2. 注入领域知识 — 避免术语混淆（如 Tecnomatix = PD/PS）
3. 后处理去重 + 过滤
4. 更精确的实体和声明提取
"""

import json
import os
import sys
import time
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent.parent
MANIFEST_PATH = BASE / "wiki" / ".understand-anything" / "intermediate" / "scan-manifest.json"
OUTPUT_DIR = BASE / "wiki" / ".understand-anything" / "intermediate"
LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
MODEL_NAME = "mlx-qwen3.5-9b-opus-distilled-v2"

SYSTEM_PROMPT = """你是小米汽车车身工程领域的知识图谱提取专家。分析以下技术文档，提取三类知识：

## 领域背景知识（必须掌握）
- **Tecnomatix** = 西门子工业软件品牌，包含 **Process Designer (PD)** 和 **Process Simulate (PS)** 两个产品。说"Tecnomatix（PD/PS）"和"西门子PD/PS"是同一件事，不是矛盾。
- **仿真评审**分多个阶段：第一次评审、第二次评审、预验收、终验收。不同阶段的要求不同，这是正常的阶段递进，不是矛盾。
- **Plant Simulation** 是 Tecnomatix 套件中的物流仿真工具，与 PD/PS 是互补关系，不是矛盾。

## 提取规则

### 1. 实体(Entities)
提取文中提到的**具体**工具、设备、软件、标准。要求：
- 名称必须是具体的产品/设备名，不能是泛称（如"机器人"太泛，应为"FANUC R-2000iC"）
- 类型: tool / device / software / standard / file_format
- 必须有明确的文档来源

### 2. 声明(Claims)
提取关键技术参数、数值标准。要求：
- 必须包含**具体数值**或**明确条件**
- 不要提取泛泛的流程描述
- 类别: parameter（参数值）/ threshold（阈值/判定标准）/ format（格式要求）

### 3. 矛盾(Contradictions)
**严格标准** — 只有以下情况才算矛盾：
- 两篇文档对**同一参数**给出了**不同的数值**（如文档A说≥5mm，文档B说≥10mm）
- 两篇文档对**同一流程**给出了**冲突的步骤**
- 两篇文档对**同一术语**给出了**不同的定义**

**以下情况不算矛盾**：
- 文档A定义了某参数，文档B没提到 → 这是"未覆盖"，不是矛盾
- 文档适用于不同阶段（如第一次评审 vs 终验收） → 这是阶段递进，不是矛盾
- 使用不同名称指代同一事物（如Tecnomatix和PD/PS） → 这是同义词，不是矛盾

## 输出格式（严格JSON）

```json
{
  "entities": [
    {"name": "具体名称", "type": "tool/device/software/standard/file_format", "summary": "一句话描述", "source_doc": "文档标题"}
  ],
  "claims": [
    {"title": "参数/标准名称", "content": "具体数值或条件", "category": "parameter/threshold/format", "source_doc": "文档标题"}
  ],
  "contradictions": [
    {"doc1": "文档A标题", "doc2": "文档B标题", "description": "具体矛盾描述", "param": "冲突参数名", "value1": "A的值（必须具体）", "value2": "B的值（必须具体）", "confidence": "high/medium"}
  ]
}
```

重要：
- contradictions 数组如果没发现真正矛盾，就返回空数组 []
- confidence=high 要求数值明确冲突；confidence=medium 要求是同一参数但表述差异
- 不要把"未提及"当作矛盾"""

USER_PROMPT_TEMPLATE = """以下是 {count} 篇技术文档，请分析它们之间的关系，提取实体、声明和矛盾：

{articles}

请以JSON格式输出结果。记住：
- 只提取有具体数值的参数声明
- 矛盾必须是两篇文档对同一参数给出不同数值，不能是"有 vs 无"
- 如果没有发现真正矛盾，contradictions 返回空数组"""


def call_llm(messages: list[dict]) -> str:
    """调用 LM Studio API。"""
    try:
        resp = requests.post(
            LM_STUDIO_URL,
            json={
                "model": MODEL_NAME,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 4000,
            },
            timeout=300,
        )
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        return msg.get("content") or msg.get("reasoning_content") or ""
    except Exception as e:
        print(f"  ⚠️ LLM 调用失败: {e}", file=sys.stderr)
        return ""


def parse_json_response(text: str) -> dict:
    """从 LLM 响应中提取 JSON。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    import re
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

    return {"entities": [], "claims": [], "contradictions": []}


def deduplicate_contradictions(contradictions: list[dict]) -> list[dict]:
    """去重并过滤假阳性矛盾。"""
    seen = set()
    filtered = []

    for c in contradictions:
        # 生成去重键
        key = tuple(sorted([c.get("doc1", ""), c.get("doc2", "")])) + (c.get("param", ""),)
        if key in seen:
            continue
        seen.add(key)

        # 过滤假阳性：检查是否是"有 vs 无"型矛盾
        v1 = c.get("value1", "").strip()
        v2 = c.get("value2", "").strip()

        # 如果其中一个值是"未定义"/"未明确"/"未包含"等，降级为 medium 或过滤
        none_indicators = ["未定义", "未明确", "未包含", "未说明", "未提及", "未规定"]
        is_none_v1 = any(ind in v1 for ind in none_indicators)
        is_none_v2 = any(ind in v2 for ind in none_indicators)

        if is_none_v1 and is_none_v2:
            # 两边都没定义，跳过
            continue
        elif is_none_v1 or is_none_v2:
            # 一边有定义一边没有，降级为 low confidence
            c["confidence"] = "low"
            c["note"] = "一方未提及，可能是覆盖范围差异而非矛盾"

        filtered.append(c)

    return filtered


def batch_articles(articles: list[dict], batch_size: int = 5) -> list[list[dict]]:
    """将文章分成批次。"""
    batches = []
    for i in range(0, len(articles), batch_size):
        batches.append(articles[i:i + batch_size])
    return batches


def format_article(doc: dict) -> str:
    """格式化单篇文章为分析输入。"""
    content = doc.get("knowledgeMeta", {}).get("content", "")[:2000]
    return f"""## {doc['name']}
分类: {doc.get('knowledgeMeta', {}).get('category', '无')}
摘要: {doc['summary']}

内容:
{content}
"""


def main():
    if not MANIFEST_PATH.exists():
        print("❌ scan-manifest.json 不存在", file=sys.stderr)
        sys.exit(1)

    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    articles = [n for n in manifest["nodes"] if n["type"] == "article"]
    print(f"📚 共 {len(articles)} 篇文章待分析")

    batches = batch_articles(articles, batch_size=5)
    print(f"📦 分为 {len(batches)} 批（每批5篇）")

    all_entities = []
    all_claims = []
    all_contradictions = []

    for i, batch in enumerate(batches):
        print(f"\n🔍 分析第 {i+1}/{len(batches)} 批（{len(batch)} 篇）...")

        articles_text = "\n".join(format_article(doc) for doc in batch)
        user_prompt = USER_PROMPT_TEMPLATE.format(count=len(batch), articles=articles_text)

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        response = call_llm(messages)
        if not response:
            print(f"  ⚠️ 第 {i+1} 批分析失败，跳过")
            continue

        result = parse_json_response(response)
        entities = result.get("entities", [])
        claims = result.get("claims", [])
        contradictions = result.get("contradictions", [])

        all_entities.extend(entities)
        all_claims.extend(claims)
        all_contradictions.extend(contradictions)

        print(f"  ✅ 提取: {len(entities)} 实体, {len(claims)} 声明, {len(contradictions)} 矛盾")

        if i < len(batches) - 1:
            time.sleep(1)

    # 后处理：去重矛盾
    print(f"\n🔧 后处理：去重矛盾...")
    all_contradictions = deduplicate_contradictions(all_contradictions)
    print(f"  去重后: {len(all_contradictions)} 条矛盾")

    # 输出结果
    output = {
        "entities": all_entities,
        "claims": all_claims,
        "contradictions": all_contradictions,
        "stats": {
            "total_articles": len(articles),
            "total_entities": len(all_entities),
            "total_claims": len(all_claims),
            "total_contradictions": len(all_contradictions),
        },
    }

    output_path = OUTPUT_DIR / "llm-analysis-v2.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"📊 分析完成!")
    print(f"  实体: {len(all_entities)}")
    print(f"  声明: {len(all_claims)}")
    print(f"  矛盾: {len(all_contradictions)}")
    print(f"📁 结果保存: {output_path}")

    if all_contradictions:
        print(f"\n🔴 发现的矛盾:")
        for c in all_contradictions:
            conf = c.get('confidence', 'unknown')
            print(f"  ⚠️ [{conf}] {c.get('doc1', '?')} vs {c.get('doc2', '?')}")
            print(f"     参数: {c.get('param', '?')}")
            print(f"     值1: {c.get('value1', '?')}")
            print(f"     值2: {c.get('value2', '?')}")
            if c.get('note'):
                print(f"     注: {c['note']}")
            print()


if __name__ == "__main__":
    main()
