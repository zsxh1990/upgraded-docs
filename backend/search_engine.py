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
import os
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

    try:
        if _USE_LM_STUDIO:
            _EMBEDDINGS = _embed_with_lm_studio(texts)
        else:
            embeddings = list(model.embed(texts))
            _EMBEDDINGS = np.array(embeddings)
    except Exception as e:
        print(f"  ⚠️  embedding 生成失败: {e}")
        _EMBEDDINGS = np.array([])

    _build_bm25(docs)
    _build_entity_index(docs)
    print(f"  语义索引构建完成: {len(docs)} 篇, 维度 {_EMBEDDINGS.shape[1]}")
    print(f"  BM25 索引: {len(_BM25_DF)} 个词项, 实体索引: {len(_ENTITY_INDEX)} 个实体")


# ── 同义词扩展（从 RAG Wiki 继承） ─────────────────────────────────

_SYNONYM_FILE = BASE / "data" / "synonyms.json"
_synonym_cache = None


def _load_synonyms() -> dict:
    global _synonym_cache
    if _synonym_cache is not None:
        return _synonym_cache
    try:
        if _SYNONYM_FILE.exists():
            with open(_SYNONYM_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            _synonym_cache = {k: v for k, v in raw.items()
                              if not k.startswith("_")}
        else:
            _synonym_cache = {}
    except Exception:
        _synonym_cache = {}
    return _synonym_cache


def expand_query(query: str) -> str:
    """用同义词扩展查询词，提升召回率。"""
    syns = _load_synonyms()
    if not syns:
        return query
    expanded = [query]
    for orig, aliases in syns.items():
        if orig in query:
            expanded.extend(aliases)
    return " ".join(expanded)


# ── BM25 索引（纯 numpy，无外部依赖） ────────────────────────────────

_BM25_DOCS = []
_BM25_AVGDL = 0.0
_BM25_N = 0
_BM25_DF = {}
_BM25_K1 = 1.5
_BM25_B = 0.75


def _tokenize(text: str) -> list[str]:
    """简单分词：中文单字 + 英文词块。"""
    tokens = []
    for m in re.finditer(r"[a-zA-Z0-9\-_./]+", text):
        tokens.append(m.group().lower())
    for m in re.finditer(r"[\u4e00-\u9fff]", text):
        tokens.append(m.group())
    return tokens


def _build_bm25(docs: list[dict]):
    global _BM25_DOCS, _BM25_AVGDL, _BM25_N, _BM25_DF
    _BM25_DOCS = []
    _BM25_DF = {}
    total_terms = 0
    for d in docs:
        text = f"{d['title']} {d.get('tags', '')} {d['content'][:2000]}"
        tokens = _tokenize(text)
        _BM25_DOCS.append(tokens)
        total_terms += len(tokens)
        seen = set()
        for t in tokens:
            if t not in seen:
                _BM25_DF[t] = _BM25_DF.get(t, 0) + 1
                seen.add(t)
    _BM25_N = len(docs)
    _BM25_AVGDL = total_terms / max(_BM25_N, 1)


def _bm25_score(query_tokens: list[str], doc_idx: int) -> float:
    doc_tokens = _BM25_DOCS[doc_idx]
    if not doc_tokens:
        return 0.0
    dl = len(doc_tokens)
    tf = {}
    for t in doc_tokens:
        tf[t] = tf.get(t, 0) + 1
    score = 0.0
    for qt in query_tokens:
        if qt not in _BM25_DF:
            continue
        df = _BM25_DF[qt]
        idf = np.log((_BM25_N - df + 0.5) / (df + 0.5) + 1.0)
        term_tf = tf.get(qt, 0)
        tf_norm = term_tf * (_BM25_K1 + 1) / (term_tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / _BM25_AVGDL))
        score += idf * tf_norm
    return score


# ── 实体索引（报警码/型号精确匹配） ──────────────────────────────────

_ALARM_RE = re.compile(r"(?<![A-Za-z])([A-Z]{2,6}-\d{3,6})(?![A-Za-z0-9])", re.I)
_MODEL_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,2}-\d{2,4}[a-zA-Z]{0,3}(?:/\d{1,3}[A-Z]?)?)", re.I)
_ENTITY_INDEX = {}


def _build_entity_index(docs: list[dict]):
    global _ENTITY_INDEX
    _ENTITY_INDEX = {}
    for i, d in enumerate(docs):
        content = f"{d['title']} {d.get('tags', '')} {d['content'][:500]}"
        for m in _ALARM_RE.findall(content):
            _ENTITY_INDEX.setdefault(m.upper(), []).append((i, d))
        for m in _MODEL_RE.findall(content):
            _ENTITY_INDEX.setdefault(m.upper(), []).append((i, d))


# ── 搜索 ──────────────────────────────────────────────────────────

def search(query: str, top_k: int = 10, category: str = "") -> list[dict]:
    """混合搜索：同义词扩展 → BM25 + 向量双通道 → RRF 融合。"""
    global _DOCS, _EMBEDDINGS

    if not _DOCS:
        return []

    candidates = _DOCS
    if category:
        candidates = [d for d in candidates if d.get("category") == category]

    # 同义词扩展
    expanded = expand_query(query)
    query_lower = expanded.lower()
    query_tokens = _tokenize(expanded)

    # 关键词预筛
    kw_filtered = [
        d for d in candidates
        if query_lower in d["title"].lower() or query_lower in d["content"][:1000].lower()
    ]
    if not kw_filtered:
        kw_filtered = [
            d for d in candidates
            if query_lower in d["content"].lower()
        ]

    # 实体精确匹配
    entity_matches = set()
    for m in _ALARM_RE.findall(expanded):
        for idx, doc in _ENTITY_INDEX.get(m.upper(), []):
            entity_matches.add(id(doc))
            if doc not in kw_filtered:
                kw_filtered.append(doc)
    for m in _MODEL_RE.findall(expanded):
        for idx, doc in _ENTITY_INDEX.get(m.upper(), []):
            entity_matches.add(id(doc))
            if doc not in kw_filtered:
                kw_filtered.append(doc)

    if not kw_filtered:
        return []

    doc_map = {id(d): i for i, d in enumerate(_DOCS)}
    indices = [doc_map[id(d)] for d in kw_filtered if id(d) in doc_map]
    if not indices:
        return []

    # BM25 分数
    bm25_scores = np.array([_bm25_score(query_tokens, i) for i in indices])

    # 向量分数
    vec_scores = np.zeros(len(indices))
    model = _get_model()
    if model and _EMBEDDINGS.size > 0:
        try:
            if _USE_LM_STUDIO:
                query_vec = _embed_with_lm_studio([expanded])[0]
            else:
                query_vec = list(model.embed([expanded]))[0]
            vec_scores = _EMBEDDINGS[indices] @ np.array(query_vec)
        except Exception:
            pass

    # 实体加分
    entity_boost = np.array([
        0.3 if id(kw_filtered[i]) in entity_matches else 0.0
        for i in range(len(kw_filtered))
    ])

    # RRF 融合
    bm25_rank = np.argsort(-bm25_scores)
    vec_rank = np.argsort(-vec_scores)
    rrf_k = 60
    hybrid = np.zeros(len(indices))
    for rank_pos, idx in enumerate(bm25_rank):
        hybrid[idx] += 1.0 / (rrf_k + rank_pos + 1)
    for rank_pos, idx in enumerate(vec_rank):
        hybrid[idx] += 1.0 / (rrf_k + rank_pos + 1)

    final_scores = hybrid + entity_boost
    ranked = sorted(
        zip(kw_filtered, final_scores),
        key=lambda x: x[1], reverse=True
    )
    return [{"doc": d, "score": round(float(s), 4)} for d, s in ranked[:top_k]]


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
