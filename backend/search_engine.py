"""语义搜索引擎 — 轻量级向量检索替代 LIKE

支持两种模式：
1. fastembed（ONNX 运行时，零 torch 依赖）
2. 本地 LM Studio（OpenAI 兼容 API）
"""

import numpy as np
from pathlib import Path
import os

# 延迟导入，让用户看到清晰的错误提示
_MODEL = None
_DOCS = []
_EMBEDDINGS = None
_USE_LM_STUDIO = False
_LM_STUDIO_URL = None


def _get_model():
    global _MODEL, _USE_LM_STUDIO, _LM_STUDIO_URL

    if _MODEL is not None:
        return _MODEL

    # 优先尝试本地 LM Studio
    lm_studio_url = os.environ.get("LM_STUDIO_URL", "http://localhost:1234/v1")
    try:
        import httpx
        # 测试 LM Studio 是否可用
        resp = httpx.get(f"{lm_studio_url}/models", timeout=2.0)
        if resp.status_code == 200:
            _USE_LM_STUDIO = True
            _LM_STUDIO_URL = lm_studio_url
            print(f"✅ 使用本地 LM Studio: {lm_studio_url}")
            _MODEL = True  # 标记为已初始化
            return _MODEL
    except Exception:
        pass

    # 回退到 fastembed
    try:
        from fastembed import TextEmbedding
        _MODEL = TextEmbedding("BAAI/bge-small-zh-v1.5")
        print("✅ 使用 fastembed 本地模型")
    except ImportError:
        print("⚠️  fastembed 未安装，降级为关键词搜索")
        print("   安装: pip install fastembed")
        _MODEL = False
    except Exception as e:
        print(f"⚠️  fastembed 模型加载失败: {e}")
        print("   降级为关键词搜索")
        _MODEL = False

    return _MODEL


def _embed_with_lm_studio(texts: list[str]) -> np.ndarray:
    """使用 LM Studio API 生成 embeddings"""
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
            # 回退到零向量
            embeddings.append([0.0] * 512)

    return np.array(embeddings)


def build_index(docs: list[dict]):
    """构建语义索引。

    参数:
      docs: [{"id": str, "title": str, "content": str, "category": str, "filename": str}, ...]
    """
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


def search(query: str, top_k: int = 10, category: str = "") -> list[dict]:
    """语义搜索，返回 [{doc, score}, ...]"""
    global _DOCS, _EMBEDDINGS

    if not _DOCS:
        return []

    # 关键词过滤（预筛）
    candidates = _DOCS
    if category:
        candidates = [d for d in candidates if d.get("category") == category]

    kw = query.lower()
    # 简单关键词匹配：检查查询是否包含在标题或内容中
    kw_filtered = [
        d for d in candidates
        if kw in d["title"].lower() or kw in d["content"][:1000].lower()
    ]

    # 如果关键词没有命中，放宽到全文
    if not kw_filtered:
        kw_filtered = [
            d for d in candidates
            if kw in d["content"].lower()
        ]

    # 如果有语义模型，对关键词命中结果重排序
    model = _get_model()
    if model and _EMBEDDINGS.size > 0 and kw_filtered:
        # 找到 kw_filtered 在原列表中的索引
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

    # 无语义模型时，关键词命中按位置粗略排序（标题命中优先）
    kw_filtered.sort(key=lambda d: (
        0 if kw in d["title"].lower() else 1,
        -len(d["content"])
    ))
    return [{"doc": d, "score": 0.0} for d in kw_filtered[:top_k]]


def detect_conflicts(title, content, threshold=0.82):
    """入库时自动检测与现有文档的潜在冲突。

    用语义向量比对，相似度 > threshold 的标记为冲突。
    同时提取重叠中文技术词供人工判断。

    返回: [{"doc": {...}, "score": 0.91, "overlap_keywords": ["碰撞","安全距离"]}, ...]
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

    import re
    conflicts = []
    for doc, score in ranked:
        if score < threshold:
            break
        if doc["title"] == title:
            continue
        doc_words = set(re.findall(r"[\u4e00-\u9fff]{2,6}", doc["content"][:1000]))
        new_words = set(re.findall(r"[\u4e00-\u9fff]{2,6}", content[:1000]))
        overlap = doc_words & new_words
        conflicts.append({
            "doc": doc,
            "score": round(float(score), 4),
            "overlap_keywords": sorted(overlap)[:10],
        })
    return conflicts[:5]
