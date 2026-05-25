#!/usr/bin/env python3
"""
解析 wiki 目录，生成 scan-manifest.json
用于替代 Understand-Anything 的 parse-knowledge-base.py
"""

import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
WIKI_DIR = BASE / "wiki"
OUTPUT_DIR = WIKI_DIR / ".understand-anything" / "intermediate"

# 基于标题关键词的分类规则
CATEGORY_RULES = [
    (["编程", "FANUC", "KUKA", "ABB"], "编程标准"),
    (["应用程序", "FDS", "搬运", "涂胶", "焊接"], "应用程序"),
    (["招标", "SOR", "框架协议", "技术要求"], "招标与框架协议"),
    (["技术规范", "技术要求", "技术标准"], "技术规范"),
    (["仿真", "虚拟调试", "离线编程", "Tecnomatix", "Plant Simulation"], "仿真"),
    (["标定", "测量", "BASE测量", "TCP校准"], "标定与测量"),
    (["需求清单", "清单"], "需求与清单"),
    (["更新", "补丁", "反馈"], "更新记录"),
    (["车身", "组态", "数模"], "车身工程"),
    (["协作机器人"], "协作机器人"),
    (["管线包", "行走轴", "换枪盘", "视觉", "控制柜", "NC柔性"], "机器人硬件"),
    (["资源命名", "命名规则", "节点层级", "数据存放", "运动机构"], "规范与标准"),
]


def classify(title: str) -> str:
    for keywords, category in CATEGORY_RULES:
        for kw in keywords:
            if kw in title:
                return category
    return "其他"


def extract_wikilinks(content: str) -> list[str]:
    """提取 [[wikilink]] 链接"""
    return [m.group(1).strip() for m in re.finditer(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]', content)]


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    nodes = []
    edges = []
    categories = {}

    # 遍历 wiki 目录中的 .md 文件
    md_files = sorted(WIKI_DIR.glob("*.md"))

    for md_file in md_files:
        # 跳过 index.md 和 log.md
        if md_file.name in ("index.md", "log.md"):
            continue

        content = md_file.read_text(encoding="utf-8")
        title = md_file.stem  # 文件名去掉 .md

        # 提取一级标题作为标题
        title_match = re.search(r"^#\s+(.+)", content, re.MULTILINE)
        if title_match:
            title = title_match.group(1).strip()

        # 分类
        category = classify(title)

        # 提取 wikilinks
        wikilinks = extract_wikilinks(content)

        # 生成节点 ID
        node_id = f"article:{title}"

        # 构建节点
        node = {
            "id": node_id,
            "type": "article",
            "name": title,
            "filePath": md_file.name,
            "summary": title,
            "tags": [category],
            "complexity": "simple",
            "knowledgeMeta": {
                "wikilinks": wikilinks,
                "category": category,
                "content": content,
                "backlinks": [],
            },
        }
        nodes.append(node)

        # 统计分类
        categories.setdefault(category, []).append(node_id)

    # 创建 topic 节点
    for cat_name, cat_nodes in categories.items():
        topic_id = f"topic:{cat_name}"
        nodes.append({
            "id": topic_id,
            "type": "topic",
            "name": cat_name,
            "summary": f"{cat_name}相关文档",
            "tags": ["topic"],
            "complexity": "simple",
        })

        # 创建 categorized_under 边
        for node_id in cat_nodes:
            edges.append({
                "source": node_id,
                "target": topic_id,
                "type": "categorized_under",
                "direction": "forward",
                "weight": 0.6,
            })

    # 统计
    stats = {
        "articles": len([n for n in nodes if n["type"] == "article"]),
        "sources": 0,
        "topics": len([n for n in nodes if n["type"] == "topic"]),
        "wikilinks": sum(len(n.get("knowledgeMeta", {}).get("wikilinks", [])) for n in nodes),
        "unresolved": 0,
    }

    # 构建 manifest
    manifest = {
        "format": "karpathy",
        "stats": stats,
        "categories": [{"name": k, "count": len(v)} for k, v in categories.items()],
        "logEntries": 0,
        "nodes": nodes,
        "edges": edges,
    }

    # 写入文件
    output_path = OUTPUT_DIR / "scan-manifest.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f"✅ Wiki 解析完成!")
    print(f"  文档: {stats['articles']}")
    print(f"  主题: {stats['topics']}")
    print(f"  Wikilinks: {stats['wikilinks']}")
    print(f"📁 输出: {output_path}")


if __name__ == "__main__":
    main()
