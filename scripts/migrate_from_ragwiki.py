#!/usr/bin/env python3
"""从 RAG Wiki ChromaDB 迁移数据到 upgraded-docs SQLite

用法 (在目标 Mac 上执行):
  python3 scripts/migrate_from_ragwiki.py

前提:
  - 旧系统 ChromaDB 位于 /home/hp/rag_chromadb (或通过 --chroma-dir 指定)
  - synonyms.json 位于同目录
  - upgraded-docs 已 ./start.sh 运行过至少一次

迁移内容:
  - ChromaDB chunks → documents 表 (按 filename 合并为完整文档)
  - 实体标签 → documents.tags
  - synonyms.json → data/synonyms.json

迁移是幂等的: 重复执行不会产生重复文档
"""

import os
import sys
import json
import hashlib
import sqlite3
import argparse
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "data" / "robot_knowledge.db"
SYNONYMS_DST = BASE / "data" / "synonyms.json"


def migrate_chromadb(chroma_dir: str, batch_size: int = 5000) -> dict:
    try:
        import chromadb
    except ImportError:
        print("chromadb: pip install chromadb")
        sys.exit(1)

    client = chromadb.PersistentClient(path=chroma_dir)
    try:
        collection = client.get_collection("wiki_docs")
    except Exception as e:
        print(f"wiki_docs: {e}")
        sys.exit(1)

    total = collection.count()
    print(f"ChromaDB: {total} chunks")

    chunks_grouped = {}
    offset = 0
    while offset < total:
        scan = collection.get(
            limit=min(batch_size, total - offset),
            offset=offset,
            include=["documents", "metadatas"],
        )
        docs = scan["documents"]
        metas = scan["metadatas"]
        if not docs:
            break

        for doc, meta in zip(docs, metas):
            fn = meta.get("filename") or meta.get("source", "unknown.md")
            chunks_grouped.setdefault(fn, []).append((doc, meta))

        offset += len(docs)
        print(f"  {offset}/{total}", end="\r")
    print()

    print(f"{len(chunks_grouped)} 个文档")

    conn = sqlite3.connect(str(DB_PATH))
    imported = 0
    skipped = 0
    total_chunks = 0

    for filename, chunks in chunks_grouped.items():
        doc_id = hashlib.md5(filename.encode()).hexdigest()[:16]
        merged_text = "\n\n".join(c[0] for c in chunks if c[0])
        title = filename.rsplit(".", 1)[0]

        all_alarms = set()
        all_models = set()
        all_manuals = set()
        all_variables = set()
        categories = set()
        content_types = set()

        for _, meta in chunks:
            if meta.get("category"):
                categories.add(meta["category"])
            if meta.get("subcategory"):
                categories.add(meta["subcategory"])
            if meta.get("content_type"):
                content_types.add(meta["content_type"])
            try:
                for a in json.loads(meta.get("entity_alarms", "[]")):
                    all_alarms.add(a)
            except (json.JSONDecodeError, TypeError):
                pass
            try:
                for m in json.loads(meta.get("entity_models", "[]")):
                    all_models.add(m)
            except (json.JSONDecodeError, TypeError):
                pass
            try:
                for m in json.loads(meta.get("entity_manuals", "[]")):
                    all_manuals.add(m)
            except (json.JSONDecodeError, TypeError):
                pass
            try:
                for v in json.loads(meta.get("entity_variables", "[]")):
                    all_variables.add(v)
            except (json.JSONDecodeError, TypeError):
                pass

        category = ", ".join(sorted(categories)) if categories else ""
        tags_parts = []
        if all_alarms:
            tags_parts.append("alarm:" + ",".join(list(all_alarms)[:20]))
        if all_models:
            tags_parts.append("model:" + ",".join(list(all_models)[:10]))
        if all_manuals:
            tags_parts.append("manual:" + ",".join(list(all_manuals)[:5]))
        if all_variables:
            tags_parts.append("var:" + ",".join(list(all_variables)[:10]))
        if content_types:
            tags_parts.append("type:" + ",".join(sorted(content_types)))
        tags_str = " | ".join(tags_parts)

        try:
            conn.execute("""
                INSERT OR REPLACE INTO documents
                    (id, title, content, category, filename, tags)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (doc_id, title, merged_text[:100000], category, filename, tags_str))
            imported += 1
            total_chunks += len(chunks)
        except Exception as e:
            print(f"  {filename}: {e}")
            skipped += 1

    conn.commit()
    conn.close()
    print(f"迁移: {imported} 文档, {total_chunks} chunks, {skipped} 跳过")
    return {"documents": imported, "chunks": total_chunks, "skipped": skipped}


def migrate_synonyms(src_path: str) -> bool:
    src = Path(src_path)
    if not src.exists():
        print(f"synonyms.json 不存在: {src_path}")
        return False
    SYNONYMS_DST.parent.mkdir(parents=True, exist_ok=True)
    SYNONYMS_DST.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    count = len([l for l in src.read_text(encoding="utf-8").split("\n") if l.strip() and not l.strip().startswith("{") and not l.strip().startswith("}") and ":" in l])
    print(f"synonyms.json 已迁移 ({count} 组)")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG Wiki → upgraded-docs 迁移")
    parser.add_argument("--chroma-dir", default="/home/hp/rag_chromadb",
                        help="ChromaDB 目录")
    parser.add_argument("--synonyms", default="",
                        help="synonyms.json 路径 (默认自动检测)")
    parser.add_argument("--batch-size", type=int, default=5000)
    parser.add_argument("--skip-synonyms", action="store_true")
    args = parser.parse_args()

    # 自动检测 synonyms.json
    syn_path = args.synonyms
    if not syn_path:
        for p in ["/mnt/c/Users/hp/synonyms.json",
                   str(Path.home() / "synonyms.json"),
                   str(BASE.parent / "synonyms.json")]:
            if Path(p).exists():
                syn_path = p
                break

    print("=" * 50)
    print("  RAG Wiki → upgraded-docs 迁移")
    print("=" * 50)

    stats = migrate_chromadb(args.chroma_dir, args.batch_size)

    if not args.skip_synonyms and syn_path:
        migrate_synonyms(syn_path)

    if stats["documents"] > 0:
        print("\n重建语义索引...")
        sys.path.insert(0, str(BASE / "backend"))
        from backend.server import rebuild_search_index
        count = rebuild_search_index()
        print(f"\n完成: cd {BASE} && ./start.sh")
