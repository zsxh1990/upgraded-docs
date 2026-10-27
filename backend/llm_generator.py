"""LLM 生成模块 — 基于检索结果生成自然语言回答。

支持 OpenAI-compatible API（OpenAI、LM Studio、Ollama 等）。
"""
from __future__ import annotations

import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 默认配置
_DEFAULT_MODEL = "gpt-4o-mini"
_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_TIMEOUT = 30.0
_MAX_CONTEXT_TOKENS = 3000  # 检索结果的最大 token 数

# 系统提示词
_SYSTEM_PROMPT = """你是一个 FANUC 工业机器人知识库助手。基于提供的检索结果回答用户问题。

规则：
1. 只基于提供的检索结果回答，不要编造信息
2. 如果检索结果中没有相关信息，明确说明"根据现有知识库，未找到相关信息"
3. 回答要简洁专业，适合工程师阅读
4. 在回答末尾标注信息来源（文档标题）
5. 对于技术参数，保留原始数值和单位"""


def _estimate_tokens(text: str) -> int:
    """粗估 token 数：中文约2 token/字，英文约0.25 token/字"""
    chinese = sum(1 for c in text if '一' <= c <= '鿿')
    other = len(text) - chinese
    return chinese * 2 + max(1, other // 4)


def _build_context(results: list[dict], max_tokens: int = _MAX_CONTEXT_TOKENS) -> str:
    """将检索结果构建为 LLM 上下文。"""
    context_parts = []
    total_tokens = 0

    for i, r in enumerate(results[:5], 1):
        doc = r.get("doc", r)
        title = doc.get("title", "未知")
        content = doc.get("content", "")[:1500]  # 限制单文档长度
        category = doc.get("category", "")

        entry = f"[文档{i}] {title}"
        if category:
            entry += f" (分类: {category})"
        entry += f"\n{content}\n"

        entry_tokens = _estimate_tokens(entry)
        if total_tokens + entry_tokens > max_tokens:
            break

        context_parts.append(entry)
        total_tokens += entry_tokens

    return "\n---\n".join(context_parts)


def generate_answer(
    query: str,
    results: list[dict],
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout: Optional[float] = None,
) -> dict:
    """基于检索结果生成自然语言回答。

    参数:
        query: 用户查询
        results: 检索结果列表，每项包含 doc 和 score
        model: 模型名称（默认从环境变量或 _DEFAULT_MODEL）
        base_url: API 基础 URL（默认从环境变量或 _DEFAULT_BASE_URL）
        api_key: API 密钥（默认从环境变量 OPENAI_API_KEY）
        timeout: 超时时间（秒）

    返回:
        dict: {"answer": str, "sources": list[str], "model": str, "error": str|None}
    """
    try:
        from openai import OpenAI
    except ImportError:
        return {
            "answer": None,
            "sources": [],
            "model": None,
            "error": "openai 包未安装，请运行: pip install openai",
        }

    # 配置
    model = model or os.environ.get("LLM_MODEL", _DEFAULT_MODEL)
    base_url = base_url or os.environ.get("LLM_BASE_URL", _DEFAULT_BASE_URL)
    api_key = api_key or os.environ.get("OPENAI_API_KEY", "sk-placeholder")
    timeout = timeout or float(os.environ.get("LLM_TIMEOUT", _DEFAULT_TIMEOUT))

    # 构建上下文
    context = _build_context(results)
    if not context.strip():
        return {
            "answer": "根据现有知识库，未找到相关信息。",
            "sources": [],
            "model": model,
            "error": None,
        }

    # 构建提示词
    user_prompt = f"""基于以下检索结果回答用户问题。

检索结果：
{context}

用户问题：{query}

请提供简洁专业的回答，并在末尾标注信息来源。"""

    # 调用 LLM
    try:
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,  # 低温度，更确定性的回答
            max_tokens=800,
        )
        answer = response.choices[0].message.content

        # 提取来源
        sources = []
        for r in results[:5]:
            doc = r.get("doc", r)
            title = doc.get("title", "")
            if title and title not in sources:
                sources.append(title)

        return {
            "answer": answer,
            "sources": sources,
            "model": model,
            "error": None,
        }

    except Exception as e:
        logger.error(f"LLM 生成失败: {e}")
        return {
            "answer": None,
            "sources": [],
            "model": model,
            "error": str(e),
        }
