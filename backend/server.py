"""Flask API 服务 — 搜索 / 冲突 / 配置 / 统计

启动: python3 server.py
访问: http://localhost:5000
"""

import json
import time
import sqlite3
import os
import sys
from pathlib import Path
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory

# ── 路径 ──
BASE = Path(__file__).resolve().parent.parent
DATA_DIR = BASE / "data"
DB_PATH = DATA_DIR / "robot_knowledge.db"
FRONTEND_DIR = BASE / "frontend"
GRAPH_PATH = BASE / "wiki" / ".understand-anything" / "knowledge-graph.json"

sys.path.insert(0, str(BASE / "backend"))
from search_engine import build_index, search as _semantic_search
import obsidian_bridge
from conflict_detector import (
    list_conflicts, get_conflict, create_conflict,
    add_solution, resolve_conflict, get_stats as conflict_stats,
)

app = Flask(__name__, static_folder=str(FRONTEND_DIR), static_url_path="")

# ── 数据库初始化 ──

def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            category TEXT DEFAULT '',
            filename TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            value TEXT NOT NULL,
            doc_id TEXT REFERENCES documents(id),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_doc_title ON documents(title)
    """)
    conn.commit()
    conn.close()

init_db()


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def rebuild_search_index():
    """从 SQLite 重建语义索引。"""
    conn = get_db()
    rows = conn.execute("SELECT id, title, content, category, filename FROM documents").fetchall()
    conn.close()
    docs = [
        {"id": r["id"], "title": r["title"], "content": r["content"],
         "category": r["category"], "filename": r["filename"]}
        for r in rows
    ]
    build_index(docs)
    return len(docs)


# ── API: 搜索 ──

@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    cat = request.args.get("category", "")
    top_k = min(int(request.args.get("top_k", 10)), 50)
    if not q:
        return jsonify({"results": [], "total": 0})

    results = _semantic_search(q, top_k=top_k, category=cat)
    out = []
    for r in results:
        d = r["doc"]
        out.append({
            "id": d["id"],
            "title": d["title"],
            "snippet": d["content"][:200],
            "category": d.get("category", ""),
            "filename": d.get("filename", ""),
            "score": r["score"],
        })
    return jsonify({"results": out, "total": len(out)})


@app.route("/api/documents")
def api_documents():
    """列出所有文档。"""
    conn = get_db()
    rows = conn.execute("SELECT id, title, category, filename, created_at FROM documents ORDER BY title").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/document/<doc_id>")
def api_document(doc_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))


@app.route("/api/categories")
def api_categories():
    conn = get_db()
    rows = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM documents WHERE category!='' GROUP BY category ORDER BY cnt DESC"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/search/suggest")
def api_suggest():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    conn = get_db()
    rows = conn.execute(
        "SELECT DISTINCT title FROM documents WHERE title LIKE ? LIMIT 8",
        (f"%{q}%",)
    ).fetchall()
    conn.close()
    return jsonify([r["title"] for r in rows])


# ── API: 冲突管理 ──

@app.route("/api/conflicts")
def api_conflicts():
    status = request.args.get("status", "")
    return jsonify(list_conflicts(status))


@app.route("/api/conflicts/<cid>")
def api_conflict_detail(cid):
    c = get_conflict(cid)
    if not c:
        return jsonify({"error": "not found"}), 404
    return jsonify(c)


@app.route("/api/conflicts", methods=["POST"])
def api_create_conflict():
    data = request.get_json()
    if not data or not data.get("title"):
        return jsonify({"error": "title required"}), 400
    cid = create_conflict(data)
    return jsonify({"id": cid}), 201


@app.route("/api/conflicts/<cid>/solution", methods=["POST"])
def api_add_solution(cid):
    data = request.get_json()
    ok = add_solution(cid, data)
    if not ok:
        return jsonify({"error": "conflict not found"}), 404
    return jsonify({"status": "ok"})


@app.route("/api/conflicts/<cid>/resolve", methods=["POST"])
def api_resolve(cid):
    data = request.get_json() or {}
    ok = resolve_conflict(cid, final_spec=data.get("final_spec", ""))
    if not ok:
        return jsonify({"error": "conflict not found"}), 404
    return jsonify({"status": "ok"})


# ── API: 配置管理 ──

@app.route("/api/configs")
def api_configs():
    doc_id = request.args.get("doc_id", "")
    conn = get_db()
    if doc_id:
        rows = conn.execute("SELECT * FROM configs WHERE doc_id=? ORDER BY name", (doc_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM configs ORDER BY name").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ── API: 统计 ──

@app.route("/api/stats")
def api_stats():
    conn = get_db()
    doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    config_count = conn.execute("SELECT COUNT(*) FROM configs").fetchone()[0]
    conn.close()
    cs = conflict_stats()
    return jsonify({
        "documents": doc_count,
        "configs": config_count,
        "conflicts": cs,
    })


# ── 前端页面 ──

@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/document.html")
def document_page():
    return send_from_directory(FRONTEND_DIR, "document.html")


@app.route("/conflicts.html")
def conflicts_page():
    return send_from_directory(FRONTEND_DIR, "conflicts.html")


@app.route("/graph.html")
def graph_page():
    return send_from_directory(FRONTEND_DIR, "graph.html")


@app.route("/api/graph")
def api_graph():
    """返回知识图谱数据。"""
    if not GRAPH_PATH.exists():
        return jsonify({"error": "知识图谱未生成，请先运行分析脚本"}), 404
    with open(GRAPH_PATH, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/detect-conflicts", methods=["POST"])
def api_detect_conflicts():
    """对知识库全量文档执行冲突检测。"""
    conn = get_db()
    rows = conn.execute("SELECT id, title, content FROM documents ORDER BY RANDOM()").fetchall()
    conn.close()

    from search_engine import detect_conflicts
    import yaml
    from datetime import datetime
    from pathlib import Path

    found = 0
    conflicts_dir = DATA_DIR / "conflicts"
    conflicts_dir.mkdir(parents=True, exist_ok=True)

    for row in rows:
        try:
            conflicts = detect_conflicts(row["title"], row["content"], threshold=0.85)
        except Exception:
            continue
        for c in conflicts:
            if c["doc"]["id"] == row["id"]:
                continue
            cid = datetime.now().strftime("CF-AUTO-%Y%m%d-%H%M%S-") + str(hash(row["id"] + c["doc"]["id"]))[-6:]
            fpath = conflicts_dir / f"{cid}.yaml"
            if fpath.exists():
                continue
            kw = ", ".join(c.get("overlap_keywords", [])[:5])
            data = {
                "title": f"疑似重复/冲突: {row['title']} ↔ {c['doc']['title']}",
                "status": "待解决",
                "proposer": "系统（全量扫描）",
                "proposed_at": datetime.now().isoformat(),
                "description": f"语义相似度 {c['score']:.0%}，重叠关键词: {kw}",
                "related_docs": [row["title"], c["doc"]["title"]],
            }
            with open(fpath, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
            found += 1
    return jsonify({"found": found})


@app.route("/rebuild-index", methods=["POST"])
def api_rebuild_index():
    count = rebuild_search_index()
    return jsonify({"indexed": count})


# ── Obsidian 深度绑定 ──

@app.route("/api/obsidian/status")
def api_obsidian_status():
    """检测 Obsidian Local REST API 是否可用。"""
    available = obsidian_bridge.is_available()
    notes = obsidian_bridge.list_notes() if available else []
    return jsonify({
        "available": available,
        "note_count": len(notes),
    })


@app.route("/api/obsidian/notes")
def api_obsidian_notes():
    """列出 Obsidian vault 中的笔记。"""
    return jsonify(obsidian_bridge.list_notes())


@app.route("/api/obsidian/sync", methods=["POST"])
def api_obsidian_sync():
    """从 Obsidian 同步笔记到知识库索引。"""
    notes = obsidian_bridge.list_notes()
    docs = obsidian_bridge.build_obsidian_index(notes)
    conn = get_db()
    imported = 0
    for d in docs:
        conn.execute("""
            INSERT OR REPLACE INTO documents (id, title, content, category, filename, tags)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (d["id"], d["title"], d["content"], d["category"],
              d["filename"], json.dumps(d.get("tags", []), ensure_ascii=False)))
        imported += 1
    conn.commit()
    conn.close()
    rebuild_search_index()
    return jsonify({"imported": imported})


@app.route("/api/obsidian/annotate", methods=["POST"])
def api_obsidian_annotate():
    """向 Obsidian 笔记追加标注。"""
    data = request.get_json() or {}
    note_path = data.get("path", "")
    annotation = data.get("annotation", "")
    if not note_path or not annotation:
        return jsonify({"error": "path and annotation required"}), 400
    ok = obsidian_bridge.write_annotation(note_path, annotation)
    return jsonify({"success": ok})


@app.route("/api/obsidian/sync-graph", methods=["POST"])
def api_obsidian_sync_graph():
    """将知识图谱数据同步回 Obsidian 笔记。"""
    if not GRAPH_PATH.exists():
        return jsonify({"error": "知识图谱未生成"}), 404

    with open(GRAPH_PATH, "r", encoding="utf-8") as f:
        graph_data = json.load(f)

    stats = obsidian_bridge.sync_knowledge_graph_to_obsidian(graph_data)
    return jsonify(stats)


@app.route("/api/documents/<doc_id>/related")
def api_document_related(doc_id):
    """获取文档的关联文件（基于 wikilink）。"""
    conn = get_db()
    row = conn.execute("SELECT content, filename FROM documents WHERE id=?", (doc_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify([])
    content = row["content"]
    filename = row["filename"] or ""

    # 解析 wikilinks
    wikilinks = obsidian_bridge.extract_wikilinks(content)
    # 加上本地文档间的显式引用
    import re
    local_refs = re.findall(r'\[([^\]]+)\]\(([^)]+)\)', content)
    for ref in local_refs:
        wikilinks.append(ref[1])

    # 查这些文件是否在知识库中
    conn = get_db()
    related = []
    for target in wikilinks:
        target_name = target.replace(".md", "").strip()
        r = conn.execute(
            "SELECT id, title, filename FROM documents WHERE filename LIKE ? OR title LIKE ? LIMIT 1",
            (f"%{target_name}%", f"%{target_name}%")
        ).fetchone()
        if r:
            related.append({"id": r["id"], "title": r["title"], "filename": r["filename"]})
    conn.close()
    return jsonify(related)


# ── API: 结构化数据（V2 新增） ──

@app.route("/api/parameters")
def api_parameters():
    """查询参数表。"""
    param = request.args.get("q", "")
    doc_id = request.args.get("doc_id", "")
    from search_engine import query_parameters
    params = query_parameters(param_name=param, doc_id=doc_id)
    return jsonify({
        "total": len(params),
        "parameters": [{
            "id": p["id"],
            "param": p["param_name"],
            "value": p["value"],
            "unit": p["unit"],
            "doc": p.get("doc_title", ""),
            "doc_id": p["doc_id"],
            "sheet": p.get("sheet_name", ""),
            "normalized": p.get("normalized", ""),
        } for p in params],
    })


@app.route("/api/parameters/<doc_id>")
def api_document_parameters(doc_id):
    """获取文档的所有参数。"""
    conn = get_db()
    rows = conn.execute(
        "SELECT param_name, value, unit, sheet_name FROM parameters WHERE doc_id=? ORDER BY sheet_name, row_idx",
        (doc_id,)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/hyperlinks")
def api_hyperlinks():
    """查询超链接表。"""
    doc_id = request.args.get("doc_id", "")
    status = request.args.get("status", "")
    conn = get_db()
    if doc_id:
        rows = conn.execute(
            "SELECT h.*, d.title as doc_title FROM hyperlinks h JOIN documents d ON d.id=h.doc_id WHERE h.doc_id=?",
            (doc_id,)
        ).fetchall()
    elif status == "pending":
        rows = conn.execute(
            "SELECT h.*, d.title as doc_title FROM hyperlinks h JOIN documents d ON d.id=h.doc_id WHERE h.fetched=0 ORDER BY h.created_at"
        ).fetchall()
    elif status == "failed":
        rows = conn.execute(
            "SELECT h.*, d.title as doc_title FROM hyperlinks h JOIN documents d ON d.id=h.doc_id WHERE h.fetched=0 AND h.fetch_error!=''"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT h.*, d.title as doc_title FROM hyperlinks h JOIN documents d ON d.id=h.doc_id ORDER BY h.created_at DESC LIMIT 50"
        ).fetchall()
    conn.close()

    links = [dict(r) for r in rows]
    total = len(links)
    fetched = sum(1 for l in links if l.get("fetched"))
    return jsonify({
        "total": total,
        "fetched": fetched,
        "pending": total - fetched,
        "links": links,
    })


@app.route("/api/conflicts/full-scan", methods=["POST"])
def api_conflicts_full_scan():
    """全库参数冲突扫描。"""
    from search_engine import full_rule_conflict_scan
    conflicts = full_rule_conflict_scan()

    from conflict_detector import create_conflict
    created = 0
    for c in conflicts:
        title = f"参数冲突: {c['param']}"
        existing = create_conflict({
            "title": title,
            "status": "待解决",
            "proposer": "系统（全库扫描）",
            "description": (
                f"参数「{c['param']}」在以下文档中存在数值差异：\n"
                f"  {c['primary_doc']}: {c['primary_value']}\n"
                f"  {c['conflict_doc']}: {c['conflict_value']}"
            ),
            "related_docs": [c["primary_doc"], c["conflict_doc"]],
        })
        created += 1

    return jsonify({
        "scanned": True,
        "conflicts_found": len(conflicts),
        "yaml_created": created,
    })


@app.route("/api/hyperlinks/fetch-pending", methods=["POST"])
def api_fetch_pending_links():
    """批量拉取未处理的超链接。"""
    max_links = int(request.args.get("max", 20))
    try:
        sys.path.insert(0, str(BASE / "scripts"))
        import hyperlink_fetcher
        results = hyperlink_fetcher.fetch_pending_links(max_links=max_links)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(results)


@app.route("/api/search/structured")
def api_search_structured():
    """混合检索：文档+参数+超链接。"""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"documents": [], "parameters": [], "links": []})
    from search_engine import search_structured
    results = search_structured(q)
    return jsonify(results)


# ── API: 文件上传 ──

UPLOAD_DIR = DATA_DIR / "uploads"
ALLOWED_EXT = {".md", ".txt", ".docx", ".xlsx", ".pptx", ".pdf"}


@app.route("/api/upload", methods=["POST"])
def api_upload():
    """上传并导入单个文档。"""
    if "file" not in request.files:
        return jsonify({"error": "no file"}), 400
    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "empty filename"}), 400

    ext = Path(f.filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        return jsonify({"status": "skip", "reason": f"不支持格式 {ext}"})

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = UPLOAD_DIR / f"_{int(time.time())}_{f.filename}"
    f.save(str(tmp_path))

    try:
        sys.path.insert(0, str(BASE / "scripts"))
        from import_docs import import_file
        result = import_file(tmp_path, fetch_links=False, skip_semantic=True)

        if result.get("skipped"):
            return jsonify({"status": "skip", "reason": result.get("reason", "")})

        # 重建索引
        count = rebuild_search_index()

        return jsonify({
            "status": "ok" if not result.get("params_count") else "ok",
            "doc_id": result["id"],
            "title": result["title"],
            "params_count": result.get("params_count", 0),
            "links_count": result.get("links_count", 0),
        })
    except Exception as e:
        return jsonify({"status": "error", "reason": str(e)}), 500
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


# ── 全局错误处理 ──

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "not found"}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "服务器内部错误，请查看服务端日志"}), 500


@app.context_processor
def inject_globals():
    """向模板注入冲突计数等全局变量。"""
    try:
        from conflict_detector import get_stats
        s = get_stats()
        conflict_count = s.get("pending", 0)
    except Exception:
        conflict_count = 0
    return dict(pending_conflicts=conflict_count)


# ── 启动 ──

if __name__ == "__main__":
    count = rebuild_search_index()
    print(f"📚 知识库: {count} 篇文档已索引")
    print(f"🌐 启动: http://localhost:5000")
    print(f"   搜索: http://localhost:5000/?q=零点标定")
    print(f"   冲突: http://localhost:5000/conflicts.html")
    app.run(host="127.0.0.1", port=5000, debug=False)
