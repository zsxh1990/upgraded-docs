#!/usr/bin/env python3
"""
合并确定性解析(scan-manifest)和LLM分析(llm-analysis)，
生成完整的 knowledge-graph.json。
"""

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
MANIFEST_PATH = BASE / "wiki" / ".understand-anything" / "intermediate" / "scan-manifest.json"
LLM_PATH = BASE / "wiki" / ".understand-anything" / "intermediate" / "llm-analysis.json"
OUTPUT_DIR = BASE / "wiki" / ".understand-anything"


def normalize_name(name: str) -> str:
    """标准化名称用于去重。"""
    return name.lower().strip().replace(" ", "-")


def main():
    # 读取确定性解析结果
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # 读取LLM分析结果
    with open(LLM_PATH, "r", encoding="utf-8") as f:
        llm = json.load(f)

    # 基础节点和边（来自确定性解析）
    nodes = manifest["nodes"]
    edges = manifest["edges"]

    # 构建现有节点ID集合
    existing_ids = {n["id"] for n in nodes}

    # 添加实体节点
    entity_names = set()
    for e in llm.get("entities", []):
        name = e.get("name", "")
        norm = normalize_name(name)
        if norm in entity_names:
            continue
        entity_names.add(norm)
        entity_id = f"entity:{norm}"
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

    # 添加声明节点
    claim_slugs = set()
    for c in llm.get("claims", []):
        title = c.get("title", "")
        slug = normalize_name(title)
        if slug in claim_slugs:
            continue
        claim_slugs.add(slug)
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

    # 添加矛盾边（contradicts）
    contradiction_pairs = set()
    for c in llm.get("contradictions", []):
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
        pair_key = tuple(sorted([source_id, target_id]))
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
            },
        })

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
        "version": "1.0.0",
        "project": {
            "name": "机器人知识库",
            "description": "小米汽车机器人技术知识库",
            "analyzedAt": "2026-05-25",
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

    print(f"✅ 知识图谱生成完成!")
    print(f"  节点: {stats['totalNodes']}")
    print(f"  边: {stats['totalEdges']}")
    print(f"  节点类型: {stats['nodeTypes']}")
    print(f"  边类型: {stats['edgeTypes']}")
    print(f"  层: {len(layers)}")
    print(f"📁 输出: {output_path}")


if __name__ == "__main__":
    main()
