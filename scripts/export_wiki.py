#!/usr/bin/env python3
"""从 SQLite 导出文档为 Karpathy 格式 wiki，生成 index.md。"""

import sqlite3
import os
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "data" / "robot_knowledge.db"
WIKI_DIR = BASE / "wiki"

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


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, title, content, category, filename, tags FROM documents ORDER BY title"
    ).fetchall()
    conn.close()

    WIKI_DIR.mkdir(parents=True, exist_ok=True)

    # 按分类组织
    categories: dict[str, list[dict]] = {}
    for r in rows:
        cat = classify(r["title"])
        doc = {
            "id": r["id"],
            "title": r["title"],
            "content": r["content"],
            "filename": r["filename"] or "",
            "category": cat,
        }
        categories.setdefault(cat, []).append(doc)

    # 写 markdown 文件
    for cat, docs in categories.items():
        for doc in docs:
            # 用标题作为文件名，去掉特殊字符
            safe_name = re.sub(r'[<>:"/\\|?*]', '_', doc["title"]).strip()
            if not safe_name:
                safe_name = doc["id"]
            filepath = WIKI_DIR / f"{safe_name}.md"

            # 写文件内容
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(f"# {doc['title']}\n\n")
                f.write(doc["content"])

    # 生成 index.md
    index_path = WIKI_DIR / "index.md"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("# 机器人知识库\n\n")
        for cat in sorted(categories.keys()):
            f.write(f"## {cat}\n\n")
            for doc in categories[cat]:
                safe_name = re.sub(r'[<>:"/\\|?*]', '_', doc["title"]).strip()
                if not safe_name:
                    safe_name = doc["id"]
                f.write(f"- [[{safe_name}|{doc['title']}]]\n")
            f.write("\n")

    # 生成 log.md
    log_path = WIKI_DIR / "log.md"
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("# 操作日志\n\n")
        f.write("## [2026-05-25] export | 从SQLite导出文档到wiki格式\n\n")

    print(f"✅ 导出完成: {len(rows)} 篇文档")
    for cat, docs in sorted(categories.items()):
        print(f"  📁 {cat}: {len(docs)} 篇")
    print(f"📁 wiki 目录: {WIKI_DIR}")
    print(f"📄 index.md: {index_path}")


if __name__ == "__main__":
    main()
