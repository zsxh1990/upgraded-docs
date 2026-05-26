#!/usr/bin/env python3
"""
对 wiki 文档做 LLM 深度分析，提取实体、声明、隐含关系（特别是矛盾）。
使用 LM Studio 本地模型。
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

SYSTEM_PROMPT = """你是知识图谱提取专家。分析以下技术文档，提取三类隐含知识：

1. **实体(Entities)**：文中提到的工具、设备、软件、标准（如 FANUC、KUKA、Tecnomatix）
2. **声明(Claims)**：关键技术参数、数值标准、断言（如"碰撞安全距离≥5mm"、"精度±0.05"）
3. **矛盾(Contradictions)**：文档间参数值不一致、流程冲突、术语定义矛盾

输出格式（严格JSON）：
```json
{
  "entities": [
    {"name": "实体名", "type": "tool/device/standard/software", "summary": "一句话描述", "source_doc": "文档标题"}
  ],
  "claims": [
    {"title": "声明标题", "content": "具体声明内容", "category": "parameter/process/definition", "source_doc": "文档标题"}
  ],
  "contradictions": [
    {"doc1": "文档A标题", "doc2": "文档B标题", "description": "矛盾描述", "param": "涉及参数", "value1": "A文档值", "value2": "B文档值"}
  ]
}
```

重要：
- 只提取有明确证据的内容，不要猜测
- 矛盾必须是两篇文档之间的真实冲突
- 参数值冲突要精确到数值
- 如果没有发现矛盾，contradictions 数组为空"""

USER_PROMPT_TEMPLATE = """以下是 {count} 篇技术文档，请分析它们之间的关系，提取实体、声明和矛盾：

{articles}

请以JSON格式输出结果。"""


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
        # Qwen3.5 可能将内容放在 reasoning_content 中
        return msg.get("content") or msg.get("reasoning_content") or ""
    except Exception as e:
        print(f"  ⚠️ LLM 调用失败: {e}", file=sys.stderr)
        return ""


def parse_json_response(text: str) -> dict:
    """从 LLM 响应中提取 JSON。"""
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json ... ``` 块
    import re
    match = re.search(r'```json\s*\n(.*?)\n\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试提取 { ... } 块
    match = re.search(r'\{[\s\S]*\}', text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return {"entities": [], "claims": [], "contradictions": []}


def batch_articles(articles: list[dict], batch_size: int = 10) -> list[list[dict]]:
    """将文章分成批次。"""
    batches = []
    for i in range(0, len(articles), batch_size):
        batches.append(articles[i:i + batch_size])
    return batches


def format_article(doc: dict) -> str:
    """格式化单篇文章为分析输入。"""
    content = doc.get("knowledgeMeta", {}).get("content", "")[:1500]
    return f"""## {doc['name']}
分类: {doc.get('knowledgeMeta', {}).get('category', '无')}
摘要: {doc['summary']}

内容:
{content}
"""


def main():
    if not MANIFEST_PATH.exists():
        print("❌ scan-manifest.json 不存在，请先运行 parse-knowledge-base.py", file=sys.stderr)
        sys.exit(1)

    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    articles = [n for n in manifest["nodes"] if n["type"] == "article"]
    print(f"📚 共 {len(articles)} 篇文章待分析")

    # 分批
    batches = batch_articles(articles, batch_size=5)
    print(f"📦 分为 {len(batches)} 批（每批最多10篇）")

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

        # 避免 API 限流
        if i < len(batches) - 1:
            time.sleep(1)

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

    output_path = OUTPUT_DIR / "llm-analysis.json"
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
            print(f"  ⚠️ {c.get('doc1', '?')} vs {c.get('doc2', '?')}")
            print(f"     {c.get('description', '')}")
            print()


if __name__ == "__main__":
    main()
