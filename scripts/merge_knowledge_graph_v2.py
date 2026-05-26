#!/usr/bin/env python3
"""
知识图谱合并 V2 — 改进版

改进点：
1. 使用 V2 分析结果（去重后）
2. 过滤低置信度矛盾
3. 实体去重（基于规范化名称）
4. 声明去重（基于标题相似度）
"""

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
MANIFEST_PATH = BASE / "wiki" / ".understand-anything" / "intermediate" / "scan-manifest.json"
LLM_PATH = BASE / "wiki" / ".understand-anything" / "intermediate" / "llm-analysis-v2.json"
OUTPUT_DIR = BASE / "wiki" / ".understand-anything"


def normalize_name(name: str) -> str:
    """标准化名称用于去重。"""
    return name.lower().strip().replace(" ", "-")


def normalize_entity_name(name: str) -> str:
    """标准化实体名称，处理同义词。"""
    name = name.lower().strip()
    # 处理常见同义词
    synonyms = {
        "tecnomatix": "pd-ps",
        "process designer": "pd-ps",
        "process simulate": "pd-ps",
        "pd": "pd-ps",
        "ps": "pd-ps",
    }
    for key, value in synonyms.items():
        if key in name:
            return value
    return name.replace(" ", "-")


def main():
    # 读取确定性解析结果
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # 读取 V2 LLM 分析结果
    with open(LLM_PATH, "r", encoding="utf-8") as f:
        llm = json.load(f)

    # 基础节点和边（来自确定性解析）
    nodes = manifest["nodes"]
    edges = manifest["edges"]

    # 构建现有节点ID集合
    existing_ids = {n["id"] for n in nodes}

    # 添加实体节点（去重）
    entity_names = set()
    entities_added = 0
    for e in llm.get("entities", []):
        name = e.get("name", "")
        norm = normalize_entity_name(name)
        if norm in entity_names:
            continue
        entity_names.add(norm)
        entity_id = f"entity:{normalize_name(name)}"
        if entity_id in existing_ids:
            continue
        nodes.append({
            "id": entity_id,
            "type": "entity",
            "name": name,
            "summary": e.get("summary", ""),
            "tags": ["entity", e.get("type", "unknown")],
            "complexity": "simple",
            "entityMeta": {
                "entityType": e.get("type", "unknown"),
                "sourceDoc": e.get("source_doc", ""),
            },
        })
        existing_ids.add(entity_id)
        entities_added += 1

    # 添加声明节点（去重）
    claim_titles = set()
    claims_added = 0
    for c in llm.get("claims", []):
        title = c.get("title", "")
        slug = normalize_name(title)
        if slug in claim_titles:
            continue
        claim_titles.add(slug)
        claim_id = f"claim:{slug}"
        if claim_id in existing_ids:
            continue
        nodes.append({
            "id": claim_id,
            "type": "claim",
            "name": title,
            "summary": c.get("content", ""),
            "tags": ["claim", c.get("category", "unknown")],
            "complexity": "simple",
            "claimMeta": {
                "category": c.get("category", "unknown"),
                "sourceDoc": c.get("source_doc", ""),
            },
        })
        existing_ids.add(claim_id)
        claims_added += 1

    # 添加矛盾边（只保留 high/medium confidence）
    contradiction_pairs = set()
    contradictions_added = 0
    for c in llm.get("contradictions", []):
        # 过滤低置信度矛盾
        confidence = c.get("confidence", "low")
        if confidence == "low":
            continue

        doc1 = c.get("doc1", "")
        doc2 = c.get("doc2", "")
        if not doc1 or not doc2:
            continue

        # 查找对应的article节点ID
        source_id = None
        target_id = None
        for n in nodes:
            if n["type"] == "article":
                if n["name"] == doc1 or doc1 in n["name"]:
                    source_id = n["id"]
                if n["name"] == doc2 or doc2 in n["name"]:
                    target_id = n["id"]

        if not source_id or not target_id:
            continue

        # 去重
        pair_key = tuple(sorted([source_id, target_id])) + (c.get("param", ""),)
        if pair_key in contradiction_pairs:
            continue
        contradiction_pairs.add(pair_key)

        edges.append({
            "source": source_id,
            "target": target_id,
            "type": "contradicts",
            "direction": "bidirectional",
            "weight": 0.9,
            "description": c.get("description", ""),
            "contradictionMeta": {
                "param": c.get("param", ""),
                "value1": c.get("value1", ""),
                "value2": c.get("value2", ""),
                "confidence": confidence,
                "note": c.get("note", ""),
            },
        })
        contradictions_added += 1

    # 构建层（基于分类）
    layers = []
    category_map = {}
    for n in nodes:
        if n["type"] == "article":
            cat = n.get("knowledgeMeta", {}).get("category", "")
            if cat:
                category_map.setdefault(cat, []).append(n["id"])

    for cat, node_ids in category_map.items():
        layers.append({
            "id": f"layer:{normalize_name(cat)}",
            "name": cat,
            "description": f"{cat}相关文档",
            "nodeIds": node_ids,
        })

    # 统计
    stats = {
        "totalNodes": len(nodes),
        "totalEdges": len(edges),
        "nodeTypes": {},
        "edgeTypes": {},
    }
    for n in nodes:
        stats["nodeTypes"][n["type"]] = stats["nodeTypes"].get(n["type"], 0) + 1
    for e in edges:
        stats["edgeTypes"][e["type"]] = stats["edgeTypes"].get(e["type"], 0) + 1

    # 构建最终图谱
    graph = {
        "version": "2.0.0",
        "project": {
            "name": "机器人知识库",
            "description": "小米汽车机器人技术知识库",
            "analyzedAt": "2026-05-25",
            "improvements": [
                "领域知识注入（Tecnomatix=PD/PS）",
                "矛盾去重（基于参数+文档对）",
                "假阳性过滤（排除'有vs无'型）",
                "实体同义词合并",
            ],
        },
        "nodes": nodes,
        "edges": edges,
        "layers": layers,
        "tour": [],
        "stats": stats,
    }

    # 写入文件
    output_path = OUTPUT_DIR / "knowledge-graph.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)

    print(f"✅ 知识图谱 V2 生成完成!")
    print(f"  节点: {stats['totalNodes']} (新增实体: {entities_added}, 新增声明: {claims_added})")
    print(f"  边: {stats['totalEdges']} (新增矛盾: {contradictions_added})")
    print(f"  节点类型: {stats['nodeTypes']}")
    print(f"  边类型: {stats['edgeTypes']}")
    print(f"  层: {len(layers)}")
    print(f"📁 输出: {output_path}")

    if contradictions_added > 0:
        print(f"\n🔴 保留的矛盾（高/中置信度）:")
        for e in edges:
            if e["type"] == "contradicts":
                meta = e.get("contradictionMeta", {})
                conf = meta.get("confidence", "unknown")
                print(f"  ⚠️ [{conf}] {e['source']} ↔ {e['target']}")
                print(f"     参数: {meta.get('param', '?')}")
                print(f"     值1: {meta.get('value1', '?')}")
                print(f"     值2: {meta.get('value2', '?')}")


if __name__ == "__main__":
    main()
