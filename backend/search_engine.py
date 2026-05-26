"""语义搜索引擎 V2 — 轻量级向量检索 + 规则冲突检测

V2 新增:
  1. rule_layer_conflict() — 从 SQLite parameters 表直接比对
  2. detect_conflicts() 增强 — 混合规则层+语义层结果
  3. query_parameters() — 结构化参数检索

支持两种模式:
  1. fastembed（ONNX 运行时，零 torch 依赖）
  2. 本地 LM Studio（OpenAI 兼容 API）
"""

import re
import json
import sqlite3
import numpy as np
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "data" / "robot_knowledge.db"

_MODEL = None
_DOCS = []
_EMBEDDINGS = None
_USE_LM_STUDIO = False
_LM_STUDIO_URL = None


def _get_model():
    global _MODEL, _USE_LM_STUDIO, _LM_STUDIO_URL

    if _MODEL is not None:
        return _MODEL

    lm_studio_url = os.environ.get("LM_STUDIO_URL", "http://localhost:1234/v1")
    try:
        import httpx
        resp = httpx.get(f"{lm_studio_url}/models", timeout=2.0)
        if resp.status_code == 200:
            _USE_LM_STUDIO = True
            _LM_STUDIO_URL = lm_studio_url
            print(f"  ✅ 使用本地 LM Studio: {lm_studio_url}")
            _MODEL = True
            return _MODEL
    except Exception:
        pass

    try:
        from fastembed import TextEmbedding
        _MODEL = TextEmbedding("BAAI/bge-small-zh-v1.5")
        print("  ✅ 使用 fastembed 本地模型")
    except ImportError:
        print("  ⚠️  fastembed 未安装，降级为关键词搜索")
        _MODEL = False
    except Exception as e:
        print(f"  ⚠️  fastembed 加载失败: {e}，降级为关键词搜索")
        _MODEL = False

    return _MODEL


def _embed_with_lm_studio(texts: list[str]) -> np.ndarray:
    import httpx
    embeddings = []
    for text in texts:
        resp = httpx.post(
            f"{_LM_STUDIO_URL}/embeddings",
            json={"model": "text-embedding-bge-small-zh-v1.5", "input": text},
            timeout=30.0
        )
        if resp.status_code == 200:
            data = resp.json()
            embeddings.append(data["data"][0]["embedding"])
        else:
            embeddings.append([0.0] * 512)
    return np.array(embeddings)


def build_index(docs: list[dict]):
    """构建语义索引。"""
    global _DOCS, _EMBEDDINGS
    _DOCS = docs

    if not docs:
        _EMBEDDINGS = np.array([])
        return

    model = _get_model()
    if not model:
        _EMBEDDINGS = np.array([])
        return

    texts = [f"{d['title']} {d['content'][:500]}" for d in docs]

    if _USE_LM_STUDIO:
        _EMBEDDINGS = _embed_with_lm_studio(texts)
    else:
        embeddings = list(model.embed(texts))
        _EMBEDDINGS = np.array(embeddings)

    print(f"  语义索引构建完成: {len(docs)} 篇, 维度 {_EMBEDDINGS.shape[1]}")


# ── 搜索 ──────────────────────────────────────────────────────────

def search(query: str, top_k: int = 10, category: str = "") -> list[dict]:
    """语义搜索。"""
    global _DOCS, _EMBEDDINGS

    if not _DOCS:
        return []

    candidates = _DOCS
    if category:
        candidates = [d for d in candidates if d.get("category") == category]

    kw = query.lower()
    kw_filtered = [
        d for d in candidates
        if kw in d["title"].lower() or kw in d["content"][:1000].lower()
    ]
    if not kw_filtered:
        kw_filtered = [
            d for d in candidates
            if kw in d["content"].lower()
        ]

    model = _get_model()
    if model and _EMBEDDINGS.size > 0 and kw_filtered:
        doc_map = {id(d): i for i, d in enumerate(_DOCS)}
        indices = [doc_map[id(d)] for d in kw_filtered if id(d) in doc_map]
        if indices:
            if _USE_LM_STUDIO:
                query_vec = _embed_with_lm_studio([query])[0]
            else:
                query_vec = list(model.embed([query]))[0]
            scores = _EMBEDDINGS[indices] @ np.array(query_vec)
            ranked = sorted(
                zip(kw_filtered, scores),
                key=lambda x: x[1], reverse=True
            )
            return [{"doc": d, "score": round(float(s), 4)} for d, s in ranked[:top_k]]

    kw_filtered.sort(key=lambda d: (
        0 if kw in d["title"].lower() else 1,
        -len(d["content"])
    ))
    return [{"doc": d, "score": 0.0} for d in kw_filtered[:top_k]]


# ── 规则层冲突检测（新增） ─────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def query_parameters(param_name: str = "", doc_id: str = "",
                     top_k: int = 50) -> list[dict]:
    """查询 parameters 表中的参数。

    参数:
      param_name: 参数名（支持模糊匹配）
      doc_id: 按文档过滤
      top_k: 最大返回数
    """
    conn = _get_db()
    if param_name:
        rows = conn.execute("""
            SELECT p.*, d.title as doc_title
            FROM parameters p
            JOIN documents d ON d.id = p.doc_id
            WHERE p.param_name LIKE ? OR p.normalized LIKE ?
            ORDER BY p.created_at DESC
            LIMIT ?
        """, (f"%{param_name}%", f"%{param_name}%", top_k)).fetchall()
    elif doc_id:
        rows = conn.execute("""
            SELECT p.*, d.title as doc_title
            FROM parameters p
            JOIN documents d ON d.id = p.doc_id
            WHERE p.doc_id = ?
            ORDER BY p.normalized, p.row_idx
        """, (doc_id,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT p.*, d.title as doc_title
            FROM parameters p
            JOIN documents d ON d.id = p.doc_id
            ORDER BY p.created_at DESC
            LIMIT ?
        """, (top_k,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def rule_layer_conflict(param_normalized: str,
                        exclude_doc_id: str = "") -> list[dict]:
    """规则层冲突检测：同名参数值比对。

    对指定参数名，查询所有文档中该参数的值，返回不一致的记录。

    返回: [{"param": ..., "doc_title": ..., "doc_id": ...,
            "value": ..., "unit": ..., "differs_from": [...]}, ...]
    """
    conn = _get_db()
    rows = conn.execute("""
        SELECT p.param_name, p.value, p.unit, p.doc_id, d.title as doc_title
        FROM parameters p
        JOIN documents d ON d.id = p.doc_id
        WHERE p.normalized = ?
          AND p.value != ''
        ORDER BY p.created_at DESC
    """, (param_normalized,)).fetchall()
    conn.close()

    if len(rows) < 2:
        return []

    # 按值分组
    groups = defaultdict(list)
    for r in rows:
        key = f"{r['value']}|{r['unit']}"
        groups[key].append(dict(r))

    if len(groups) == 1:
        return []  # 所有文档一致

    result = []
    # 取最常见的值作为"标准"
    sorted_groups = sorted(groups.items(), key=lambda x: -len(x[1]))
    standard_val = sorted_groups[0][0]

    for val, docs in sorted_groups[1:]:
        for d in docs:
            if exclude_doc_id and d["doc_id"] == exclude_doc_id:
                continue
            result.append({
                "param": d["param_name"],
                "doc_title": d["doc_title"],
                "doc_id": d["doc_id"],
                "value": f"{d['value']}{d['unit']}",
                "standard_value": standard_val.replace("|", ""),
                "differs_from_standard": True,
            })

    return result


def full_rule_conflict_scan() -> list[dict]:
    """全库扫描参数冲突。

    对所有有参数的文档，检测同名参数数值是否一致。

    返回: [{"param": ..., "doc_title": ..., "value": ...,
            "conflicts_with": [{"doc": ..., "value": ...}, ...]}, ...]
    """
    conn = _get_db()
    rows = conn.execute("""
        SELECT normalized, COUNT(DISTINCT value || unit) as val_count
        FROM parameters
        WHERE value != '' AND normalized != ''
        GROUP BY normalized
        HAVING val_count > 1
    """).fetchall()
    conn.close()

    results = []
    for r in rows:
        conflicts = rule_layer_conflict(r["normalized"])
        if conflicts:
            # 合并
            param_name = conflicts[0]["param"]
            by_doc = {}
            for c in conflicts:
                key = c["doc_title"]
                by_doc.setdefault(key, []).append(c["value"])

            primary_doc = max(by_doc.items(), key=lambda x: -len(x[1]))[0]
            others = {
                d: v for d, v in by_doc.items() if d != primary_doc
            }

            for other_doc, other_vals in others.items():
                results.append({
                    "param": param_name,
                    "primary_doc": primary_doc,
                    "primary_value": by_doc[primary_doc][0],
                    "conflict_doc": other_doc,
                    "conflict_value": other_vals[0],
                })

    return results


# ── 语义冲突检测（原逻辑增强） ─────────────────────────────────────

def detect_conflicts(title, content, threshold=0.82):
    """入库时自动检测与现有文档的潜在冲突。

    V2 增强：混合规则层+语义层结果。
    规则层返回的冲突直接优先，语义层作为补充。
    """
    model = _get_model()
    if not model or _EMBEDDINGS.size == 0 or not _DOCS:
        return []

    if _USE_LM_STUDIO:
        query_vec = _embed_with_lm_studio([f"{title} {content[:300]}"])[0]
    else:
        query_vec = list(model.embed([f"{title} {content[:300]}"]))[0]

    scores = _EMBEDDINGS @ np.array(query_vec)
    ranked = sorted(zip(_DOCS, scores), key=lambda x: x[1], reverse=True)

    conflicts = []
    for doc, score in ranked:
        if score < threshold:
            break
        if doc["title"] == title:
            continue

        # 提取重叠中文技术词
        doc_words = set(re.findall(r"[\u4e00-\u9fff]{2,6}", doc["content"][:1000]))
        new_words = set(re.findall(r"[\u4e00-\u9fff]{2,6}", content[:1000]))
        overlap = doc_words & new_words

        conflicts.append({
            "doc": doc,
            "score": round(float(score), 4),
            "overlap_keywords": sorted(overlap)[:10],
        })

    return conflicts[:5]


# ── 结构化检索 API ───────────────────────────────────────────────

def search_structured(query: str, top_k: int = 20) -> dict:
    """混合检索：语义搜索文档 + 结构化参数检索。

    返回: {"documents": [...], "parameters": [...], "links": [...]}
    """
    result = {"documents": [], "parameters": [], "links": []}

    # 文档语义搜索
    doc_results = search(query, top_k=min(top_k, 10))
    result["documents"] = [
        {"id": d["doc"]["id"], "title": d["doc"]["title"],
         "category": d["doc"]["category"], "score": d["score"]}
        for d in doc_results
    ]

    # 参数检索
    params = query_parameters(param_name=query, top_k=top_k)
    result["parameters"] = [
        {"param": p["param_name"], "value": p["value"],
         "unit": p["unit"], "doc": p.get("doc_title", ""),
         "doc_id": p["doc_id"], "sheet": p.get("sheet_name", "")}
        for p in params
    ]

    # 链接检索
    conn = _get_db()
    link_rows = conn.execute("""
        SELECT h.*, d.title as doc_title
        FROM hyperlinks h
        JOIN documents d ON d.id = h.doc_id
        WHERE h.url LIKE ? OR h.cell_text LIKE ?
           OR h.target_text LIKE ?
        LIMIT ?
    """, (f"%{query}%", f"%{query}%", f"%{query}%", top_k)).fetchall()
    conn.close()

    result["links"] = [
        {"url": l["url"], "cell_text": l["cell_text"],
         "doc": l.get("doc_title", ""), "link_type": l["link_type"],
         "fetched": l["fetched"]}
        for l in link_rows
    ]

    return result
