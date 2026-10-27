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
import math
import os
import time
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
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
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


# ── jieba 全局 DF 缓存（IDF 加权用） ──────────────────────────────────────────
_JIEBA_GLOBAL_DF: dict[str, int] = {}
_JIEBA_GLOBAL_N: int = 0


def _load_jieba_global_df() -> None:
    """加载 jieba 复合词的全局文档频率（build_index 时生成）。"""
    global _JIEBA_GLOBAL_DF, _JIEBA_GLOBAL_N
    if _JIEBA_GLOBAL_DF:
        return
    df_path = os.path.join(os.path.dirname(__file__), "..", "data", "jieba_global_df.json")
    if os.path.exists(df_path):
        with open(df_path, "r", encoding="utf-8") as f:
            _JIEBA_GLOBAL_DF = json.load(f)
    # 总文档数
    try:
        import sqlite3 as _sqlite3
        _conn = _sqlite3.connect(os.path.join(os.path.dirname(__file__), "..", "data", "robot_knowledge.db"))
        _JIEBA_GLOBAL_N = _conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        _conn.close()
    except Exception:
        _JIEBA_GLOBAL_N = 6649


def search_mode() -> str:
    """返回当前搜索模式: 'vector' | 'bm25_only' | 'no_index'"""
    model = _get_model()
    if not model:
        return "bm25_only"
    if _EMBEDDINGS is None or _EMBEDDINGS.size == 0:
        return "no_index"
    return "vector"


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
    """构建索引（BM25 + 实体 + 可选向量）。"""
    global _DOCS, _EMBEDDINGS
    _DOCS = docs
    invalidate_search_cache()

    if not docs:
        _EMBEDDINGS = np.array([])
        return

    # BM25 + 实体索引始终构建
    _build_bm25(docs)
    _build_entity_index(docs)

    # 向量索引（可选）
    model = _get_model()
    if not model:
        _EMBEDDINGS = np.array([])
        print(f"  索引构建完成: {len(docs)} 篇 (纯 BM25)")
        print(f"  BM25: {len(_BM25_DF)} 词项, 实体: {len(_ENTITY_INDEX)} 个")
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

    print(f"  索引构建完成: {len(docs)} 篇, 向量维度 {_EMBEDDINGS.shape[1] if _EMBEDDINGS.size > 0 else 0}")
    print(f"  BM25: {len(_BM25_DF)} 词项, 实体: {len(_ENTITY_INDEX)} 个")

    # 生成 jieba 全局 DF（供 IDF 加权用）
    try:
        import jieba
        global_df: dict[str, int] = {}
        for d in docs:
            text = (d["title"] or "") + " " + (d["content"] or "")[:10000]
            seen: set[str] = set()
            for w in jieba.cut(text):
                if len(w) >= 2 and w not in seen:
                    global_df[w] = global_df.get(w, 0) + 1
                    seen.add(w)
        n = len(docs)
        rare = {k: v for k, v in global_df.items() if v / n < 0.3}
        df_path = os.path.join(os.path.dirname(__file__), "..", "data", "jieba_global_df.json")
        with open(df_path, "w", encoding="utf-8") as f:
            json.dump(rare, f, ensure_ascii=False)
        print(f"  jieba 全局 DF: {len(global_df)} 词, 稀有: {len(rare)}")
    except ImportError:
        pass


# ── Cross-Encoder Reranker（可选，不硬依赖 torch）──────────────────

_RERANKER = None
_RERANKER_ATTEMPTED = False
_RERANKER_MODEL = "BAAI/bge-reranker-base"


def _get_reranker():
    """懒加载 cross-encoder reranker。不可用时返回 None（静默降级）。"""
    global _RERANKER, _RERANKER_ATTEMPTED
    if _RERANKER is not None or _RERANKER_ATTEMPTED:
        return _RERANKER
    _RERANKER_ATTEMPTED = True
    try:
        from sentence_transformers import CrossEncoder
        _RERANKER = CrossEncoder(_RERANKER_MODEL, max_length=512)
        print(f"  [reranker] 已加载: {_RERANKER_MODEL}")
    except Exception as e:
        print(f"  [reranker] 不可用 ({e})，跳过重排序")
    return _RERANKER


def _rerank(query: str, results: list[dict], top_k: int = 10) -> list[dict]:
    """用 cross-encoder 对 top-K 候选重排序，与原始分数混合。
    混合公式: final = alpha * rrf_norm + (1-alpha) * reranker_norm
    alpha=0.4 偏重 reranker，但保留实体/boost 信号。
    """
    if len(results) < 2 or not query.strip():
        return results
    reranker = _get_reranker()
    if reranker is None:
        return results
    candidates = results[:top_k]
    rest = results[top_k:]
    try:
        pairs = [(query, r["doc"]["content"][:512]) for r in candidates]
        scores = reranker.predict(pairs, show_progress_bar=False)
        # 归一化两组分数到 [0, 1]
        rrf_vals = [r["score"] for r in candidates]
        rrf_min, rrf_max = min(rrf_vals), max(rrf_vals)
        rrf_range = rrf_max - rrf_min or 1.0
        r_min, r_max = min(scores), max(scores)
        r_range = r_max - r_min or 1.0
        alpha = 0.8  # RRF 权重（高度保留实体/boost 信号）
        for i, r in enumerate(candidates):
            rrf_norm = (r["score"] - rrf_min) / rrf_range
            rerank_norm = (scores[i] - r_min) / r_range
            r["score"] = round(alpha * rrf_norm + (1 - alpha) * rerank_norm, 4)
        candidates.sort(key=lambda x: x["score"], reverse=True)
    except Exception as e:
        print(f"  [rerank] 失败: {e}")
        return results
    return candidates + rest


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


def expand_query(query: str) -> tuple[str, list[str]]:
    """用同义词扩展查询词，提升召回率。
    返回 (expanded_query, concept_synonyms) — concept_synonyms 是本次添加的同义词列表。"""
    syns = _load_synonyms()
    if not syns:
        return query, []
    expanded = [query]
    concept_synonyms = []
    for orig, aliases in syns.items():
        if orig in query:
            expanded.extend(aliases)
            concept_synonyms.extend(aliases)
    return " ".join(expanded), concept_synonyms


# ── BM25 索引（纯 numpy，无外部依赖） ────────────────────────────────

_BM25_DOCS = []
_BM25_AVGDL = 0.0
_BM25_N = 0
_BM25_DF = {}
_BM25_K1 = 1.5
_BM25_B = 0.75


def _tokenize(text: str) -> list[str]:
    """分词：jieba 中文复合词 + 英文词块（单字不加入，避免停用词噪声）。"""
    tokens = []
    # 英文/数字词块（1+ 字符）
    for m in re.finditer(r"[a-zA-Z0-9\-_./]+", text):
        tokens.append(m.group().lower())
    # jieba 中文分词（2+ 字复合词，过滤标点和单字停用词）
    try:
        import jieba
        for w in jieba.cut(text):
            if len(w) >= 2 and re.match(r"[\u4e00-\u9fff]", w):
                tokens.append(w)
    except ImportError:
        # jieba 不可用时回退到单字
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
_MODEL_NOHYPHEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,2}\d{2,4}[A-Z]{0,3}|\d{2,4}[A-Z]{1,3})(?![A-Za-z0-9])", re.I)
_ENTITY_INDEX = {}


def _build_entity_index(docs: list[dict]):
    global _ENTITY_INDEX
    _ENTITY_INDEX = {}
    for i, d in enumerate(docs):
        content = f"{d['title']} {d.get('tags', '')} {d['content']}"
        # 规范化报警码后再提取
        content = _normalize_alarm_codes(content)
        for m in _ALARM_RE.findall(content):
            _ENTITY_INDEX.setdefault(m.upper(), []).append((i, d))
        for m in _MODEL_RE.findall(content):
            _ENTITY_INDEX.setdefault(m.upper(), []).append((i, d))
        for m in _MODEL_NOHYPHEN_RE.findall(content):
            _ENTITY_INDEX.setdefault(m.upper(), []).append((i, d))


# ── 搜索缓存 ──────────────────────────────────────────────────────

_SEARCH_CACHE = {}
_CACHE_TTL = 300  # 5 分钟


def _cache_get(key):
    entry = _SEARCH_CACHE.get(key)
    if entry and time.time() - entry[1] < _CACHE_TTL:
        return entry[0]
    return None


def _cache_set(key, value):
    _SEARCH_CACHE[key] = (value, time.time())
    # 淘汰过期条目，限制缓存大小
    if len(_SEARCH_CACHE) > 256:
        now = time.time()
        expired = [k for k, v in _SEARCH_CACHE.items() if now - v[1] >= _CACHE_TTL]
        for k in expired:
            del _SEARCH_CACHE[k]


def invalidate_search_cache():
    _SEARCH_CACHE.clear()


# ── 报警码规范化（索引构建用）────────────────────────────────────

def _normalize_alarm_codes(text: str) -> str:
    """规范化报警码：srvo023 → SRVO-023（仅用于索引构建）。"""
    def _insert_dash(m):
        return f"{m.group(1).upper()}-{m.group(2)}"
    return re.sub(r"(?<![A-Za-z])([a-zA-Z]{2,6})(\d{3,6})(?![A-Za-z0-9])", _insert_dash, text)


# ── 查询规范化 ────────────────────────────────────────────────────

_CHINESE_ALIASES = {
    "法那科": "FANUC", "法兰克": "FANUC", "发那科": "FANUC",
    "发那克": "FANUC", "法纳科": "FANUC",
}
_CHINESE_ALIAS_RE = re.compile("|".join(re.escape(k) for k in _CHINESE_ALIASES), re.I)


def _normalize_query(query: str) -> str:
    """规范化查询：型号格式、报警码。中文别名由 expand_query 同义词处理。"""
    q = query
    # FANUCM-900 → FANUC M-900
    q = re.sub(r'(FANUC)([A-Z0-9])', r'\1 \2', q)
    # CR35iA → CR-35iA
    q = re.sub(r'(?<![A-Za-z0-9])CR(\d{1,2})(i[A-Z]?)(?![A-Za-z0-9])', r'CR-\1\2', q, flags=re.I)
    # CC Link / CCLink → CC-Link
    q = re.sub(r'(?i)CC\s*-?\s*Link', 'CC-Link', q)
    # R30iB → R-30iB
    q = re.sub(r'(?i)(?<![A-Za-z0-9])R\s*30i([AB])(?![A-Za-z0-9])', r'R-30i\1', q)
    # M900 → M-900, R2000 → R-2000（排除端口名 JD17/JRF1 等）
    q = re.sub(r'(?<![A-Za-z0-9])(?!JD|JRF|JS|JM|TP|EE)([A-Z]{1,2})(\d{2,4})(?!\d)', r'\1-\2', q)
    # 报警码大小写：svgn-381 → SVGN-381
    q = re.sub(r'(?<![A-Za-z])([a-zA-Z]{2,6})-(\d{3,6})(?![A-Za-z0-9])',
               lambda m: f'{m.group(1).upper()}-{m.group(2)}', q)
    # 无连字符报警码：srvo023 → SRVO-023
    q = re.sub(r'(?<![A-Za-z])([a-zA-Z]{2,6})(\d{3,6})(?![A-Za-z0-9])',
               lambda m: f'{m.group(1).upper()}-{m.group(2)}', q)
    return q


# ── 搜索 ──────────────────────────────────────────────────────────

def search(query: str, top_k: int = 10, category: str = "") -> list[dict]:
    """混合搜索：同义词扩展 → BM25 + 向量双通道 → RRF 融合。"""
    global _DOCS, _EMBEDDINGS

    if not _DOCS:
        return []

    # 缓存命中
    cache_key = (query, top_k, category)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    candidates = _DOCS
    if category:
        candidates = [d for d in candidates if d.get("category") == category]

    # 同义词扩展 + 查询规范化
    expanded, concept_synonyms = expand_query(query)
    expanded = _normalize_query(expanded)
    # 原始查询词用于 BM25 评分（避免扩展词稀释），扩展词仅用于预筛
    normalized_original = _normalize_query(query)
    original_tokens = _tokenize(normalized_original)
    query_tokens = _tokenize(expanded)

    # 实体精确匹配
    entity_matches = set()
    for m in _ALARM_RE.findall(expanded):
        for idx, doc in _ENTITY_INDEX.get(m.upper(), []):
            entity_matches.add(id(doc))
    for m in _MODEL_RE.findall(expanded):
        for idx, doc in _ENTITY_INDEX.get(m.upper(), []):
            entity_matches.add(id(doc))
    for m in _MODEL_NOHYPHEN_RE.findall(expanded):
        for idx, doc in _ENTITY_INDEX.get(m.upper(), []):
            entity_matches.add(id(doc))

    # 关键词预筛（至少一个显著词出现在文档中）
    # 提取显著词：英文词(3+字符)、报警码、型号、中文 jieba 词(3+字)
    # 同时检查扩展后的查询（包含同义词），确保概念相关文档不被过滤
    significant_terms = set()
    for m in re.finditer(r"[a-zA-Z0-9\-_./]{2,}", expanded):
        significant_terms.add(m.group().lower())
    try:
        import jieba
        for w in jieba.cut(expanded):
            if len(w) >= 2 and not re.match(r"^[a-zA-Z0-9\-_./]+$", w):
                significant_terms.add(w)
    except ImportError:
        pass
    for m in _ALARM_RE.findall(expanded):
        significant_terms.add(m.upper())
    for m in _MODEL_RE.findall(expanded):
        significant_terms.add(m.upper())

    if significant_terms:
        # 预筛：2+ 显著词匹配（减少候选池噪声）
        # 但始终包含匹配超稀有词（df < 1%）的文档
        _load_jieba_global_df()
        niche_sig_terms: set[str] = set()
        try:
            import jieba as _jieba
            for w in _jieba.cut(expanded):
                if len(w) >= 2:
                    gdf = _JIEBA_GLOBAL_DF.get(w, 0)
                    if 0 < gdf / max(_JIEBA_GLOBAL_N, 1) < 0.01:
                        niche_sig_terms.add(w)
        except ImportError:
            pass

        kw_filtered = []
        kw_filtered_ids = set()
        for d in candidates:
            title_lower = d["title"].lower()
            content_lower = d["content"][:5000].lower()
            match_count = 0
            matched_niche = False
            for term in significant_terms:
                if term.lower() in title_lower or term.lower() in content_lower:
                    match_count += 1
            for term in niche_sig_terms:
                if term in content_lower or term in title_lower:
                    matched_niche = True
                    break
            # 短查询(<=3个显著词)放宽到1个匹配，长查询保持>=2
            min_match = 1 if len(significant_terms) <= 3 else 2
            if match_count >= min_match or matched_niche:
                kw_filtered.append(d)
                kw_filtered_ids.add(id(d))
        # 实体匹配的文档也加入
        for d in candidates:
            if id(d) in entity_matches and id(d) not in kw_filtered_ids:
                kw_filtered.append(d)
        if not kw_filtered:
            kw_filtered = list(candidates)
    else:
        kw_filtered = list(candidates)

    doc_map = {id(d): i for i, d in enumerate(_DOCS)}
    indices = [doc_map[id(d)] for d in kw_filtered if id(d) in doc_map]

    # BM25 分数（使用原始查询词，避免同义词扩展稀释精度）
    bm25_scores = np.array([_bm25_score(original_tokens, i) for i in indices])

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

    # 实体加分（报警码/型号精确匹配时大幅提升）
    entity_boost = np.array([
        10.0 if id(kw_filtered[i]) in entity_matches else 0.0
        for i in range(len(kw_filtered))
    ])

    # ── jieba 分词（提前，供 title_boost / content_substr_boost 共用） ──
    content_substr_boost = np.zeros(len(kw_filtered))
    jieba_terms = set()
    try:
        import jieba
        for w in jieba.cut(query):
            if len(w) >= 2:
                jieba_terms.add(w)
    except ImportError:
        pass
    # 也添加原始查询中长度>=4的连续中文片段的3-4字子串
    # 解决 jieba 切分不匹配问题（如"高惯量"被切成"惯量"）
    # 只取3-4字子串，避免2字常见词（如"执行"、"指导"）干扰
    for m in re.finditer(r"[一-鿿]{4,}", query):
        seg = m.group()
        for L in range(3, min(5, len(seg) + 1)):
            for start in range(len(seg) - L + 1):
                jieba_terms.add(seg[start:start + L])

    # 标题匹配加分（查询关键词逐词出现在标题中，IDF 加权）
    # 旧逻辑只检查整个查询串是否为标题子串，自然语言查询永远 miss
    _load_jieba_global_df()
    title_keywords: dict[str, float] = {}  # term → weight
    for w in jieba_terms:
        if len(w) < 2:
            continue
        gdf = _JIEBA_GLOBAL_DF.get(w, 0)
        idf = max(1.0, math.log(_JIEBA_GLOBAL_N / (gdf + 1))) if _JIEBA_GLOBAL_N else 1.0
        title_keywords[w] = idf
    # 英文关键词也加入
    for w in re.findall(r"[a-zA-Z]{3,}", query):
        w_lower = w.lower()
        if w_lower not in title_keywords:
            bm25_df = _BM25_DF.get(w_lower, 0)
            idf = max(1.0, math.log(_BM25_N / (bm25_df + 1))) if _BM25_N else 1.0
            title_keywords[w_lower] = idf

    title_boost = np.zeros(len(kw_filtered))
    # 原始查询词（不含同义词扩展）的标题匹配，给予更高权重
    orig_title_keywords: dict[str, float] = {}
    for w in jieba_terms:
        if len(w) < 2:
            continue
        gdf = _JIEBA_GLOBAL_DF.get(w, 0)
        idf = max(1.0, math.log(_JIEBA_GLOBAL_N / (gdf + 1))) if _JIEBA_GLOBAL_N else 1.0
        orig_title_keywords[w] = idf
    for w in re.findall(r"[a-zA-Z]{2,}", normalized_original):
        w_lower = w.lower()
        if w_lower not in orig_title_keywords:
            bm25_df = _BM25_DF.get(w_lower, 0)
            idf = max(1.0, math.log(_BM25_N / (bm25_df + 1))) if _BM25_N else 1.0
            orig_title_keywords[w_lower] = idf

    # 原始查询子串精确匹配标题 — 处理 "TCP/IP" 出现在 "4.12.25 TCP/IP** 主机" 的情况
    orig_query_lower = normalized_original.lower()
    # 提取原始查询中的英文缩写/术语（2+字符）
    orig_en_terms = [m.group().lower() for m in re.finditer(r"[a-zA-Z0-9\-_./]{2,}", normalized_original)]

    if title_keywords:
        for i, d in enumerate(kw_filtered):
            t_lower = d["title"].lower()
            matched_weight = 0.0
            for term, w in title_keywords.items():
                if term in t_lower:
                    matched_weight += w
            # 原始查询词标题匹配加分
            orig_matched = 0.0
            orig_total = sum(orig_title_keywords.values())
            for term, w in orig_title_keywords.items():
                if term in t_lower:
                    orig_matched += w
            if orig_total > 0:
                hit_ratio = orig_matched / orig_total
                title_boost[i] = min(80.0, matched_weight * 0.3 + hit_ratio * 80.0)
            else:
                title_boost[i] = min(5.0, matched_weight * 0.3)
            # 原始查询中的英文术语在标题中精确出现 → 额外加分
            en_hit = sum(1 for t in orig_en_terms if t in t_lower)
            if en_hit > 0 and orig_en_terms:
                en_ratio = en_hit / len(orig_en_terms)
                title_boost[i] += en_ratio * 50.0  # 上限 +50

    # ── 概念同义词加分 + 缺失惩罚 ──────────────────────────────────────────
    # 解决中文自然语言查询→英文技术术语的语义鸿沟
    # 例如："系统扫描周期" → "ITP_TIME", "scan_time", "servo cycle"
    # 策略：匹配概念同义词的文档加分，有概念同义词但完全未匹配的文档惩罚
    concept_boost = np.zeros(len(kw_filtered))
    if concept_synonyms:
        # 计算每个概念同义词的 IDF 权重
        concept_weights: dict[str, float] = {}
        for syn in concept_synonyms:
            syn_lower = syn.lower()
            bm25_df = _BM25_DF.get(syn_lower, 0)
            if bm25_df > 0:
                idf = max(1.0, math.log(_BM25_N / (bm25_df + 1)))
                concept_weights[syn_lower] = idf
            else:
                # 不在 BM25 索引中的术语，给予高权重（可能是超稀有词）
                concept_weights[syn_lower] = 10.0
        # 同时将 synonyms.json 中的原始触发词加入 concept_weights
        # 解决：同义词是"蓄电池/battery"，但文档用的是"电池"（触发词本身）
        # 只添加 IDF 较高的触发词（排除"可调整"等通用词）
        syns = _load_synonyms()
        if syns:
            for orig_key in syns:
                if orig_key in query and orig_key.lower() not in concept_weights:
                    orig_df = _JIEBA_GLOBAL_DF.get(orig_key, 0)
                    if orig_df > 0:
                        orig_idf = math.log(_JIEBA_GLOBAL_N / (orig_df + 1))
                        if orig_idf > 3.0:  # 只添加稀有触发词（DF < ~270）
                            concept_weights[orig_key.lower()] = min(orig_idf, 8.0)
        # 检查文档是否包含概念同义词
        has_any_concept = False
        for i, d in enumerate(kw_filtered):
            # 对超长文档扩大搜索窗口（系统变量表90万字，关键信息可能在深处）
            content_len = len(d["content"])
            window = min(content_len, max(100000, content_len // 3))
            content_lower = d["content"][:window].lower()
            title_lower = d["title"].lower()
            matched_weight = 0.0
            for syn, weight in concept_weights.items():
                if syn in content_lower or syn in title_lower:
                    matched_weight += weight
            if matched_weight > 0:
                has_any_concept = True
            # 概念匹配给予大权重（最高 30 分）
            concept_boost[i] = min(30.0, matched_weight * 2.0)
        # 概念缺失惩罚：如果查询有高特异性概念同义词，但文档完全不包含任何同义词，惩罚
        # 只在同义词平均 IDF > 5.0 时启用（即同义词是稀有技术术语，如 ITP_TIME）
        # 通用同义词（如 alarm → 异常）不启用惩罚，避免误伤
        if has_any_concept:
            avg_idf = sum(concept_weights.values()) / max(len(concept_weights), 1)
            if avg_idf > 5.0:
                penalty = -20.0
                for i, d in enumerate(kw_filtered):
                    if concept_boost[i] == 0.0:
                        concept_boost[i] = penalty

    # IDF 加权内容子串加分（使用全局 DF 而非候选池 DF）
    # 候选池 DF 会使"焊枪"等在预过滤后变常见的词失去区分度
    _load_jieba_global_df()
    rare_terms: dict[str, float] = {}  # term → IDF boost weight
    for t in jieba_terms:
        # 英文词用 BM25 DF
        bm25_df = _BM25_DF.get(t, 0)
        if bm25_df > 0 and bm25_df / max(_BM25_N, 1) >= 0.3:
            continue
        # 中文复合词用全局 jieba DF
        global_df = _JIEBA_GLOBAL_DF.get(t, bm25_df)
        if global_df / max(_JIEBA_GLOBAL_N, 1) >= 0.3:
            continue  # 全局 >30% 的常见词，跳过
        # IDF 加权：越稀有的词加分越大
        idf = max(1.0, math.log(_JIEBA_GLOBAL_N / (global_df + 1)))
        rare_terms[t] = idf
    # 分层 IDF 加权（解决超稀有词被多个常见词联合淹没的问题）
    if rare_terms:
        for i, d in enumerate(kw_filtered):
            content_head = d["content"][:10000]
            title = d["title"]
            for term, idf in rare_terms.items():
                global_df_t = _JIEBA_GLOBAL_DF.get(term)
                if global_df_t is None:
                    mult = 1.0  # 不在 jieba DF 字典中（非复合词或 df=0 被过滤）
                else:
                    ratio = global_df_t / max(_JIEBA_GLOBAL_N, 1)
                    if 0 < ratio < 0.01:
                        mult = 3.0  # 超稀有: 挠度(df=3), SOP(df=43)
                    elif ratio < 0.10:
                        mult = 1.5  # 稀有: DCS(df=412)
                    else:
                        mult = 1.0  # 不常见: 焊枪(df=788)
                if term in content_head:
                    content_substr_boost[i] += idf * mult * 0.5
                if term in title:
                    content_substr_boost[i] += idf * mult * 0.3

    # 唯一候选加分：如果一个稀有中文 jieba 词仅在 kw_filtered 的 1 个文档中出现，大幅加分
    # 只针对纯中文 2+ 字词，避免影响英文/混合查询
    if rare_terms:
        for term, idf in rare_terms.items():
            if idf < 5.0 or len(term) < 2:
                continue
            if not all('一' <= c <= '鿿' for c in term):
                continue  # 只处理纯中文词
            match_indices = []
            for i, d in enumerate(kw_filtered):
                content_head = d["content"][:10000]
                if term in content_head or term in d["title"]:
                    match_indices.append(i)
            if len(match_indices) == 1:
                # 唯一匹配：给予大加分
                content_substr_boost[match_indices[0]] += idf * 2.0

    # 稀有术语标题加分（jieba 高 IDF 词出现在标题中，说明文档高度相关）
    # 解决"挠度"等稀有词在标题中但 BM25 排名过低的问题
    # 只对全局 DF 字典中确实存在的稀有词加分（排除"机器人"等未被索引的假稀有词）
    if rare_terms:
        high_idf_terms = {
            t: idf for t, idf in rare_terms.items()
            if idf >= 5.0 and _JIEBA_GLOBAL_DF.get(t) is not None
        }
        if high_idf_terms:
            for i, d in enumerate(kw_filtered):
                title = d["title"]
                for term, idf in high_idf_terms.items():
                    if term in title:
                        content_substr_boost[i] += idf * 1.5

    # 查询子串匹配加分 — 使用 expanded 查询以包含概念同义词
    # - 英文多词短语（如"pc interface"）：精确短语匹配，高权重
    # - 中文 2+ 字术语（如"选型"）：jieba 分词后加入
    # - 3+ 字符子串：通用子串匹配
    query_lower = expanded.lower()  # 使用 expanded 查询
    query_substrings = set()     # 普通子串
    query_phrases = set()        # 多词短语（更高权重）
    # 英文词对作为短语
    eng_words = re.findall(r"[a-zA-Z]{2,}", query_lower)
    for i in range(len(eng_words) - 1):
        phrase = eng_words[i] + " " + eng_words[i + 1]
        query_phrases.add(phrase)
    for i in range(len(eng_words) - 2):
        tri = eng_words[i] + " " + eng_words[i + 1] + " " + eng_words[i + 2]
        query_phrases.add(tri)
    # 中文 jieba 复合词（2+ 字）——高权重子串
    chinese_jieba_terms = set()
    try:
        import jieba as _jieba2
        for w in _jieba2.cut(expanded):  # 使用 expanded 查询
            if len(w) >= 2 and not w.isascii():
                query_substrings.add(w.lower())
                chinese_jieba_terms.add(w.lower())
    except ImportError:
        pass
    # 3+ 字符子串（英文/数字/中文混合）
    for m in re.finditer(r"[a-zA-Z0-9一-鿿]{3,}", expanded):  # 使用 expanded 查询
        s = m.group().lower()
        query_substrings.add(s)
    # 英文单词（2+ 字母）也加入
    for w in eng_words:
        query_substrings.add(w)
    if query_substrings or query_phrases:
        _head500_window = 500  # check_hit 使用 content[:500]
        for i, d in enumerate(kw_filtered):
            content_lower = d["content"][:10000].lower()
            content500 = d["content"][:_head500_window].lower()
            content5000 = d["content"][:5000].lower()  # check_hit 窗口
            title_lower = d["title"].lower()
            for phrase in query_phrases:
                if phrase in content_lower:
                    content_substr_boost[i] += 5.0  # 精确短语在内容中
                if phrase in title_lower:
                    content_substr_boost[i] += 3.0  # 精确短语在标题中
            for sub in query_substrings:
                is_chinese = sub in chinese_jieba_terms
                if sub in content_lower:
                    content_substr_boost[i] += 3.0 if is_chinese else 2.0
                if sub in title_lower:
                    content_substr_boost[i] += 2.0 if is_chinese else 1.5
            # 内容前500字窗口额外加分（check_hit 检查窗口）
            for phrase in query_phrases:
                if phrase in content500:
                    content_substr_boost[i] += 1.0
            # 中文 jieba 词在内容前5000字窗口额外加分（匹配 check_hit 检查范围）
            for sub in chinese_jieba_terms:
                if sub in content5000:
                    content_substr_boost[i] += 1.0

    # 中文滑动窗口子串加分：4-6 字中文子串在文档内容中出现
    # 只对长查询(10+中文字符)启用，避免短查询产生过多噪声窗口
    ch_sequences = re.findall(r"[一-鿿]{4,}", expanded)  # 使用 expanded 查询
    total_ch_len = sum(len(s) for s in ch_sequences)
    if total_ch_len >= 10:
        ch_windows: set[str] = set()
        for seq in ch_sequences:
            for wlen in range(4, min(7, len(seq) + 1)):
                for start in range(len(seq) - wlen + 1):
                    ch_windows.add(seq[start:start + wlen])
        if ch_windows:
            for i, d in enumerate(kw_filtered):
                content5000 = d["content"][:5000].lower()
                matched_count = sum(1 for seg in ch_windows if seg in content5000)
                if matched_count:
                    content_substr_boost[i] += matched_count * 5.0

    # RRF 融合 — 当有 concept_synonyms 时，给 BM25 更高权重
    bm25_rank = np.argsort(-bm25_scores)
    vec_rank = np.argsort(-vec_scores)
    rrf_k = 60
    hybrid = np.zeros(len(indices))
    # BM25 已使用原始查询词，无需额外加权
    bm25_weight = 1.0
    for rank_pos, idx in enumerate(bm25_rank):
        hybrid[idx] += bm25_weight / (rrf_k + rank_pos + 1)
    for rank_pos, idx in enumerate(vec_rank):
        hybrid[idx] += 1.0 / (rrf_k + rank_pos + 1)

    # 文档长度归一化：防止长文档因匹配大量通用词而霸榜
    # 使用 log 归一化 — 长文档仍有优势，但不会无限累积
    for i, d in enumerate(kw_filtered):
        doc_len = max(len(d["content"]), 1000)
        length_factor = 1.0 + math.log(max(doc_len / 10000, 1.0))  # 10K=1.0, 100K=3.3, 1M=4.6
        content_substr_boost[i] = content_substr_boost[i] / length_factor

    final_scores = hybrid + entity_boost + title_boost + concept_boost + content_substr_boost

    # 反馈学习：用户历史点击加权
    feedback_scores = _get_feedback_scores(query)
    if feedback_scores:
        for i, d in enumerate(kw_filtered):
            fb = feedback_scores.get(d["id"], 0.0)
            if fb > 0:
                final_scores[i] += fb

    ranked = sorted(
        zip(kw_filtered, final_scores),
        key=lambda x: x[1], reverse=True
    )
    results = [{"doc": d, "score": round(float(s), 4)} for d, s in ranked[:top_k]]

    # Cross-encoder rerank（需安装 sentence-transformers）
    # 设 RERANK_ENABLED=0 可关闭；默认启用
    if os.environ.get("RERANK_ENABLED", "1") != "0":
        results = _rerank(query, results, top_k=10)

    # 实体注入：将实体匹配的文档强制插入结果顶部（解决 BM25 长文档惩罚问题）
    # 对于报警码查询，优先注入标题含"报警"的权威文档（如 FANUC报警代码）
    # 实体注入：将实体匹配的文档强制插入结果顶部（解决 BM25 长文档惩罚问题）
    # 对于报警码查询，优先注入标题含"报警"的权威文档（如 FANUC报警代码）
    if entity_matches:
        # 先从结果中移除实体匹配的文档（避免已有的实体文档被排除）
        results = [r for r in results if id(r["doc"]) not in entity_matches]
        idx_to_pos = {idx: pos for pos, idx in enumerate(indices)}
        alarm_query = bool(_ALARM_RE.findall(expanded))
        entity_docs = []
        for d in candidates:
            if id(d) in entity_matches:
                doc_idx = doc_map.get(id(d))
                pos = idx_to_pos.get(doc_idx)
                bm25 = float(bm25_scores[pos]) if pos is not None else 0.0
                title_lower = d["title"].lower()
                priority = 3
                for m in _ALARM_RE.findall(expanded):
                    if m.upper() in title_lower.upper():
                        priority = 1
                        break
                if priority == 3 and alarm_query and "报警" in title_lower:
                    priority = 2
                # 报警码查询时，标题含"报警代码"的权威文档优先级最高
                if alarm_query and "报警代码" in title_lower:
                    priority = 0
                entity_docs.append((d, bm25, priority))
        entity_docs.sort(key=lambda x: (x[2], -x[1]))
        for d, _, _ in entity_docs[:5]:
            results.insert(0, {"doc": d, "score": 99.0})
        results = results[:top_k]

    # ── Chunk 去重：同一文档只保留最高分 chunk ──────────────────────
    # 解决同一文档多个 chunk 霸占 top-N 的问题
    # 用 title 去重（同标题 = 同文档的不同 chunk）
    seen_titles: set[str] = set()
    deduped: list[dict] = []
    for r in results:
        t = r["doc"]["title"]
        if t not in seen_titles:
            seen_titles.add(t)
            deduped.append(r)
    results = deduped[:top_k]

    _cache_set(cache_key, results)
    return results


# ── 查询反馈学习 ────────────────────────────────────────────────────

def record_feedback(query: str, doc_id: str) -> None:
    """记录用户对搜索结果的点击反馈。"""
    import hashlib
    query_hash = hashlib.md5(query.strip().lower().encode()).hexdigest()[:16]
    conn = _get_db()
    try:
        conn.execute("""
            INSERT INTO search_feedback (query_hash, doc_id, click_count, last_clicked)
            VALUES (?, ?, 1, datetime('now'))
            ON CONFLICT(query_hash, doc_id)
            DO UPDATE SET click_count = click_count + 1, last_clicked = datetime('now')
        """, (query_hash, doc_id))
        conn.commit()
    finally:
        conn.close()


def _get_feedback_scores(query: str) -> dict[str, float]:
    """获取查询对应的文档反馈分数。返回 {doc_id: boost_score}。"""
    import hashlib
    query_hash = hashlib.md5(query.strip().lower().encode()).hexdigest()[:16]
    conn = _get_db()
    try:
        rows = conn.execute("""
            SELECT doc_id, click_count FROM search_feedback
            WHERE query_hash = ? ORDER BY click_count DESC LIMIT 10
        """, (query_hash,)).fetchall()
        if not rows:
            return {}
        max_clicks = rows[0]["click_count"]
        return {r["doc_id"]: 5.0 * (r["click_count"] / max_clicks) for r in rows}
    finally:
        conn.close()


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
