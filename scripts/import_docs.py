#!/usr/bin/env python3
"""文档导入脚本 — 将 Markdown/Office 文档导入知识库

用法:
  python3 import_docs.py docs/                    # 导入整个目录
  python3 import_docs.py docs/spec.md             # 导入单个文件
  python3 import_docs.py docs/ --category=规范    # 指定分类
"""

import os
import sys
import re
import hashlib
import sqlite3
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "data" / "robot_knowledge.db"

SUPPORTED = {".md", ".txt", ".docx", ".xlsx", ".pptx"}


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".md":
        return path.read_text(encoding="utf-8")
    elif ext == ".txt":
        return path.read_text(encoding="utf-8")
    elif ext == ".docx":
        try:
            from docx import Document
            return "\n".join(p.text for p in Document(str(path)).paragraphs)
        except ImportError:
            return f"[需要 python-docx: pip install python-docx]\n{path}"
    elif ext == ".xlsx":
        try:
            from openpyxl import load_workbook
            texts = []
            wb = load_workbook(str(path), read_only=True)
            for sheet in wb.sheetnames:
                ws = wb[sheet]
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        texts.append(" | ".join(cells))
            return "\n".join(texts)
        except ImportError:
            return f"[需要 openpyxl: pip install openpyxl]\n{path}"
    elif ext == ".pptx":
        try:
            from pptx import Presentation
            texts = []
            for slide in Presentation(str(path)).slides:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        texts.append(shape.text)
            return "\n".join(texts)
        except ImportError:
            return f"[需要 python-pptx: pip install python-pptx]\n{path}"
    return ""


def extract_title(content: str, filename: str) -> str:
    """从内容中提取标题，fallback 到文件名。"""
    # Markdown 一级标题
    m = re.search(r"^#\s+(.+)", content, re.MULTILINE)
    if m:
        return m.group(1).strip()
    # 文件名（去掉后缀）
    return filename.rsplit(".", 1)[0]


def _auto_create_conflict(new_title: str, conflict: dict):
    """自动创建冲突 YAML 记录。"""
    from datetime import datetime
    target = conflict["doc"]
    cid = datetime.now().strftime("CF-AUTO-%Y%m%d-%H%M%S")
    file_path = BASE / "data" / "conflicts" / f"{cid}.yaml"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    kw = ", ".join(conflict.get("overlap_keywords", [])[:5])
    data = {
        "title": f"疑似重复/冲突: {new_title} ↔ {target['title']}",
        "status": "待解决",
        "proposer": "系统（自动检测）",
        "proposed_at": datetime.now().isoformat(),
        "description": (
            f"导入文档「{new_title}」与现有文档「{target['title']}」"
            f"语义相似度 {conflict['score']:.0%}。\n"
            f"重叠关键词: {kw}\n"
            f"请人工确认是否为重复定义或存在规范冲突。"
        ),
        "related_docs": [new_title, target["title"]],
        "sources": [{"doc": target["title"], "score": conflict["score"]}],
    }
    import yaml
    with open(file_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
    return cid


def import_file(path: Path, category: str = "") -> dict:
    """导入单个文件，返回记录信息。"""
    text = extract_text(path)
    if not text or len(text) < 20:
        return {"skipped": True, "reason": "内容过短"}

    title = extract_title(text, path.name)
    doc_id = hashlib.md5(str(path.resolve()).encode()).hexdigest()[:16]

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        INSERT OR REPLACE INTO documents (id, title, content, category, filename)
        VALUES (?, ?, ?, ?, ?)
    """, (doc_id, title, text, category, path.name))
    conn.commit()
    conn.close()

    # 自动检测与现有文档的冲突
    _HAS_SEARCH = False
    try:
        sys.path.insert(0, str(BASE / "backend"))
        import search_engine
        conflicts = search_engine.detect_conflicts(title, text)
        for c in conflicts:
            cid = _auto_create_conflict(title, c)
            print(f"  ⚠️  检测到冲突 → {cid}")
        _HAS_SEARCH = True
    except Exception:
        pass

    return {"id": doc_id, "title": title, "skipped": False}


def import_dir(dir_path: Path, category: str = ""):
    """递归导入目录。"""
    files = sorted(dir_path.rglob("*"))
    files = [f for f in files if f.suffix.lower() in SUPPORTED]
    total = len(files)
    imported = 0
    for i, f in enumerate(files, 1):
        # 从目录结构推断分类
        rel = f.relative_to(dir_path)
        subcat = str(rel.parent) if str(rel.parent) != "." else category
        result = import_file(f, category=subcat)
        status = "OK" if not result["skipped"] else "SKIP"
        print(f"  [{status}] {f.name}")
        if not result["skipped"]:
            imported += 1
    print(f"\n导入完成: {imported}/{total}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="导入文档到知识库")
    parser.add_argument("path", help="文件或目录路径")
    parser.add_argument("--category", default="", help="分类标签")
    args = parser.parse_args()

    p = Path(args.path)
    if not p.exists():
        print(f"路径不存在: {p}")
        sys.exit(1)

    if p.is_file():
        result = import_file(p, category=args.category)
        print(f"  导入: {result.get('title', p.name)} ({'OK' if not result.get('skipped') else result['reason']})")
    else:
        import_dir(p, category=args.category)

    # 重建语义索引
    print("\n重建语义索引...")
    from backend.server import rebuild_search_index
    count = rebuild_search_index()
    print(f"语义索引重建完成: {count} 篇")
