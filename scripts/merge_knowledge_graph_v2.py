#!/usr/bin/env python3
"""
知识图谱合并 V3 — 支持丰富边类型

改进点（相比 V2）：
1. has_entity 边：文章 → 实体
2. has_claim 边：文章 → 声明
3. relates_to 边：实体间关系 + 跨文档关系
4. 新实体类型：process, configuration
"""

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
MANIFEST_PATH = BASE / "wiki" / ".understand-anything" / "intermediate" / "scan-manifest.json"
LLM_PATH = BASE / "wiki" / ".understand-anything" / "intermediate" / "llm-analysis-v2.json"
OUTPUT_DIR = BASE / "wiki" / ".understand-anything"


def normalize_name(name: str) -> str:
    return name.lower().strip().replace(" ", "-")


def normalize_entity_name(name: str) -> str:
    name = name.lower().strip()
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


def build_article_id_map(nodes: list[dict]) -> dict[str, str]:
    """构建 文档名 → article节点ID 的映射。"""
    name_to_id = {}
    for n in nodes:
        if n["type"] == "article":
            name_to_id[n["name"]] = n["id"]
            # 也用小写做模糊匹配
            name_to_id[n["name"].lower()] = n["id"]
    return name_to_id


def find_article_id(doc_name: str, name_to_id: dict[str, str]) -> str | None:
    """查找文章节点ID，支持精确和模糊匹配。"""
    if doc_name in name_to_id:
        return name_to_id[doc_name]
    # 模糊匹配
    for name, nid in name_to_id.items():
        if doc_name in name or name in doc_name:
            return nid
    return None


RELATION_WEIGHTS = {
    "shares_entity": 0.5,
    "references": 0.6,
    "depends_on": 0.7,
    "extends": 0.8,
    "uses": 0.6,
    "configures": 0.6,
    "requires": 0.7,
    "produces": 0.5,
}


def main():
    # 读取确定性解析结果
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    # 读取 V3 LLM 分析结果
    with open(LLM_PATH, "r", encoding="utf-8") as f:
        llm = json.load(f)

    nodes = manifest["nodes"]
    edges = manifest["edges"]
    existing_ids = {n["id"] for n in nodes}

    # 构建文档名→ID映射
    name_to_id = build_article_id_map(nodes)

    # ── 添加实体节点（去重）──
    entity_names = set()
    entities_added = 0
    entity_id_map = {}  # name → entity_id, for relationship resolution

    for e in llm.get("entities", []):
        name = e.get("name", "")
        norm = normalize_entity_name(name)
        if norm in entity_names:
            continue
        entity_names.add(norm)
        entity_id = f"entity:{normalize_name(name)}"
        if entity_id in existing_ids:
            entity_id_map[norm] = entity_id
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
        entity_id_map[norm] = entity_id
        entities_added += 1

    # ── 添加声明节点（去重）──
    claim_titles = set()
    claims_added = 0
    claim_id_map = {}  # title → claim_id

    for c in llm.get("claims", []):
        title = c.get("title", "")
        slug = normalize_name(title)
        if slug in claim_titles:
            continue
        claim_titles.add(slug)
        claim_id = f"claim:{slug}"
        if claim_id in existing_ids:
            claim_id_map[slug] = claim_id
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
        claim_id_map[slug] = claim_id
        claims_added += 1

    # ── 添加 has_entity 边（文章 → 实体）──
    has_entity_count = 0
    for e in llm.get("entities", []):
        source_doc = e.get("source_doc", "")
        article_id = find_article_id(source_doc, name_to_id)
        if not article_id:
            continue
        norm = normalize_entity_name(e.get("name", ""))
        target_id = entity_id_map.get(norm)
        if not target_id:
            continue
        edge_key = f"{article_id}|has_entity|{target_id}"
        # 简单去重
        if any(ed.get("source") == article_id and ed.get("target") == target_id and ed.get("type") == "has_entity" for ed in edges):
            continue
        edges.append({
            "source": article_id,
            "target": target_id,
            "type": "has_entity",
            "direction": "directed",
            "weight": 0.8,
        })
        has_entity_count += 1

    # ── 添加 has_claim 边（文章 → 声明）──
    has_claim_count = 0
    for c in llm.get("claims", []):
        source_doc = c.get("source_doc", "")
        article_id = find_article_id(source_doc, name_to_id)
        if not article_id:
            continue
        slug = normalize_name(c.get("title", ""))
        target_id = claim_id_map.get(slug)
        if not target_id:
            continue
        if any(ed.get("source") == article_id and ed.get("target") == target_id and ed.get("type") == "has_claim" for ed in edges):
            continue
        edges.append({
            "source": article_id,
            "target": target_id,
            "type": "has_claim",
            "direction": "directed",
            "weight": 0.8,
        })
        has_claim_count += 1

    # ── 添加文档内关系边（实体 → 实体，relates_to）──
    intra_rel_count = 0
    for rel in llm.get("relationships", []):
        if rel.get("type") in ("references", "depends_on", "shares_entity", "extends"):
            continue  # 这些是跨文档关系，下面处理
        from_norm = normalize_entity_name(rel.get("from", ""))
        to_norm = normalize_entity_name(rel.get("to", ""))
        from_id = entity_id_map.get(from_norm)
        to_id = entity_id_map.get(to_norm)
        if not from_id or not to_id or from_id == to_id:
            continue
        weight = RELATION_WEIGHTS.get(rel.get("relation", ""), 0.5)
        edges.append({
            "source": from_id,
            "target": to_id,
            "type": "relates_to",
            "direction": "directed",
            "weight": weight,
            "description": f"{rel.get('relation', 'related')}: {rel.get('evidence', '')}",
        })
        intra_rel_count += 1

    # ── 添加跨文档关系边（文章 → 文章，relates_to）──
    cross_doc_count = 0
    for rel in llm.get("cross_doc_relations", []):
        from_name = rel.get("from", "")
        to_name = rel.get("to", "")
        from_id = find_article_id(from_name, name_to_id)
        to_id = find_article_id(to_name, name_to_id)
        if not from_id or not to_id or from_id == to_id:
            continue
        rel_type = rel.get("type", "references")
        weight = RELATION_WEIGHTS.get(rel_type, 0.5)
        edges.append({
            "source": from_id,
            "target": to_id,
            "type": "relates_to",
            "direction": "bidirectional",
            "weight": weight,
            "description": f"{rel_type}: {rel.get('evidence', '')}",
            "relationMeta": {
                "relationType": rel_type,
                "sharedEntities": rel.get("shared_entities", []),
                "evidence": rel.get("evidence", ""),
            },
        })
        cross_doc_count += 1

    # ── 添加矛盾边（只保留 high/medium confidence）──
    contradiction_pairs = set()
    contradictions_added = 0
    for c in llm.get("contradictions", []):
        confidence = c.get("confidence", "low")
        if confidence == "low":
            continue

        doc1 = c.get("doc1", "")
        doc2 = c.get("doc2", "")
        if not doc1 or not doc2:
            continue

        source_id = find_article_id(doc1, name_to_id)
        target_id = find_article_id(doc2, name_to_id)

        if not source_id or not target_id:
            continue

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

    # ── 构建层（基于分类）──
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

    # ── 统计 ──
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

    # ── 构建最终图谱 ──
    graph = {
        "version": "3.0.0",
        "project": {
            "name": "机器人知识库",
            "description": "小米汽车机器人技术知识库",
            "analyzedAt": "2026-05-28",
            "improvements": [
                "分节提取（消除2000字符截断）",
                "丰富知识类型（process/configuration/safety/constraint）",
                "has_entity/has_claim 边（文章→实体/声明）",
                "relates_to 边（实体间+跨文档关系）",
                "跨文档关系（references/depends_on/shares_entity/extends）",
            ],
        },
        "nodes": nodes,
        "edges": edges,
        "layers": layers,
        "tour": [],
        "stats": stats,
    }

    output_path = OUTPUT_DIR / "knowledge-graph.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=2)

    print(f"✅ 知识图谱 V3 生成完成!")
    print(f"  节点: {stats['totalNodes']} (新增实体: {entities_added}, 新增声明: {claims_added})")
    print(f"  边: {stats['totalEdges']}")
    print(f"    has_entity: {has_entity_count}")
    print(f"    has_claim: {has_claim_count}")
    print(f"    relates_to(文档内): {intra_rel_count}")
    print(f"    relates_to(跨文档): {cross_doc_count}")
    print(f"    contradicts: {contradictions_added}")
    print(f"  节点类型: {stats['nodeTypes']}")
    print(f"  边类型: {stats['edgeTypes']}")
    print(f"  层: {len(layers)}")
    print(f"📁 输出: {output_path}")


if __name__ == "__main__":
    main()
