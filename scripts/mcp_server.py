#!/usr/bin/env python3
"""upgraded-docs MCP Server — local knowledge base search for Claude Code, Cursor, etc.

Exposes tools:
  upgradeddocs.search(query, category?, top_k?)
  upgradeddocs.get_document(doc_id)
  upgradeddocs.list_categories()
  upgradeddocs.get_stats()

Usage:
    # As MCP server (stdio transport)
    python3 scripts/mcp_server.py

    # In Claude Code settings.json:
    {
      "mcpServers": {
        "upgraded-docs": {
          "command": "python3",
          "args": ["<your-repo-path>/scripts/mcp_server.py"]
        }
      }
    }
"""
from __future__ import annotations

import json
import sys
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

# Import search engine
try:
    from search_engine import build_index, search as _semantic_search
    HAS_SEARCH = True
except ImportError:
    HAS_SEARCH = False

# Import LLM generator
try:
    from llm_generator import generate_answer
    HAS_LLM = True
except ImportError:
    HAS_LLM = False

import sqlite3

DB_PATH = REPO_ROOT / "data" / "robot_knowledge.db"


def get_db():
    """Get database connection."""
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


def handle_search(args: dict) -> dict:
    """Search the knowledge base."""
    query = args.get("query", "")
    category = args.get("category", "")
    top_k = args.get("top_k", 10)

    if not query:
        return {
            "error": "query is required",
            "hint": "Try: {\"query\": \"电池更换\"}",
            "examples": [
                "{\"query\": \"电池更换\"}",
                "{\"query\": \"ITP_TIME\", \"category\": \"SOFTWARE\"}",
                "{\"query\": \"焊接\", \"top_k\": 5}"
            ],
        }

    if not HAS_SEARCH:
        return {"error": "Search engine unavailable"}

    try:
        results = _semantic_search(query, top_k=top_k, category=category)
        return {
            "results": results,
            "total": len(results),
            "query": query,
            "category": category or "all",
        }
    except Exception as e:
        return {"error": str(e)}


def handle_get_document(args: dict) -> dict:
    """Get a document by ID."""
    doc_id = args.get("doc_id", "")

    if not doc_id:
        return {
            "error": "doc_id is required",
            "hint": "Use upgradeddocs_search first to find document IDs",
        }

    try:
        conn = get_db()
        row = conn.execute(
            "SELECT id, title, content, category, filename, tags FROM documents WHERE id = ?",
            (doc_id,)
        ).fetchone()
        conn.close()

        if not row:
            return {"error": f"Document not found: {doc_id}"}

        return {
            "id": row["id"],
            "title": row["title"],
            "content": row["content"][:8000],  # Truncate for MCP context
            "category": row["category"],
            "filename": row["filename"],
            "tags": row["tags"],
        }
    except Exception as e:
        return {"error": str(e)}


def handle_list_categories(args: dict) -> dict:
    """List all categories with document counts."""
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT category, COUNT(*) as count FROM documents GROUP BY category ORDER BY count DESC"
        ).fetchall()
        conn.close()

        categories = [
            {"name": row["category"], "count": row["count"]}
            for row in rows
        ]
        return {"categories": categories, "total": len(categories)}
    except Exception as e:
        return {"error": str(e)}


def handle_get_stats(args: dict) -> dict:
    """Get knowledge base statistics."""
    try:
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        categories = conn.execute(
            "SELECT category, COUNT(*) as count FROM documents GROUP BY category"
        ).fetchall()
        conn.close()

        return {
            "total_documents": total,
            "categories": {row["category"]: row["count"] for row in categories},
            "search_engine": "available" if HAS_SEARCH else "unavailable",
        }
    except Exception as e:
        return {"error": str(e)}


def handle_record_feedback(args: dict) -> dict:
    """Record user click feedback for a search result."""
    query = args.get("query", "")
    doc_id = args.get("doc_id", "")

    if not query or not doc_id:
        return {"error": "Both query and doc_id are required"}

    if not HAS_SEARCH:
        return {"error": "Search engine unavailable"}

    try:
        from search_engine import record_feedback
        record_feedback(query, doc_id)
        return {"status": "recorded", "query": query, "doc_id": doc_id}
    except Exception as e:
        return {"error": str(e)}


def handle_ask(args: dict) -> dict:
    """Ask a question and get an LLM-generated answer based on search results."""
    query = args.get("query", "")
    top_k = args.get("top_k", 5)
    model = args.get("model")

    if not query:
        return {"error": "query is required"}

    if not HAS_SEARCH:
        return {"error": "Search engine unavailable"}

    if not HAS_LLM:
        return {"error": "LLM generator unavailable (openai not installed)"}

    try:
        # Search for relevant documents
        results = _semantic_search(query, top_k=top_k)

        if not results:
            return {
                "answer": "根据现有知识库，未找到相关信息。",
                "sources": [],
                "query": query,
            }

        # Generate answer
        llm_result = generate_answer(query, results, model=model)

        return {
            "answer": llm_result.get("answer", "生成失败"),
            "sources": llm_result.get("sources", []),
            "query": query,
            "model": llm_result.get("model"),
            "error": llm_result.get("error"),
        }
    except Exception as e:
        return {"error": str(e)}


# ── MCP Resources ──
RESOURCES = [
    {
        "uri": "upgradeddocs://categories",
        "name": "Categories",
        "description": "List all knowledge categories with document counts",
        "mimeType": "application/json",
    },
    {
        "uri": "upgradeddocs://stats",
        "name": "Statistics",
        "description": "Knowledge base statistics and health",
        "mimeType": "application/json",
    },
]


def handle_resources_list() -> list:
    """Return available resources."""
    return RESOURCES


def handle_resources_read(uri: str) -> dict:
    """Read a resource by URI."""
    if uri == "upgradeddocs://categories":
        return handle_list_categories({})

    elif uri == "upgradeddocs://stats":
        return handle_get_stats({})

    return {"error": f"Unknown resource: {uri}"}


# ── MCP Tools ──
TOOLS = [
    {
        "name": "upgradeddocs_search",
        "description": (
            "Search the FANUC robot knowledge base by query string. Use when you need to find "
            "documents about robot programming, alarms, maintenance, specifications, etc. "
            "Input semantics: query is required; category optionally filters by document category; "
            "top_k limits results (default 10). Output schema: JSON with results[] containing "
            "title, snippet, score, category, and document ID. Error cases: missing query or "
            "unavailable search engine. Side effects: none. Auth: none."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Required search query (e.g. '电池更换', 'ITP_TIME', '焊接参数')"
                },
                "category": {
                    "type": "string",
                    "description": "Optional category filter (e.g. 'SOFTWARE', '编程标准')"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum results to return. Defaults to 10."
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "upgradeddocs_get_document",
        "description": (
            "Fetch a document by its ID. Use after upgradeddocs_search returns a result, "
            "or when a document ID is explicitly referenced. "
            "Input semantics: doc_id is required. "
            "Output schema: JSON with id, title, content (truncated to 8000 chars), category, "
            "filename, and tags. Error cases: missing doc_id or document not found. "
            "Side effects: none. Auth: none."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_id": {
                    "type": "string",
                    "description": "Document ID from search results (e.g. '37fa81f1d1ae19ae')"
                },
            },
            "required": ["doc_id"],
        },
    },
    {
        "name": "upgradeddocs_list_categories",
        "description": (
            "List all knowledge categories with document counts. Use to understand the "
            "knowledge base structure or filter searches by category. "
            "Input semantics: none required. "
            "Output schema: JSON with categories[] containing name and count. "
            "Error cases: none. Side effects: none. Auth: none."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "upgradeddocs_get_stats",
        "description": (
            "Get knowledge base statistics including total document count, category breakdown, "
            "and search engine status. Use to check system health. "
            "Input semantics: none required. "
            "Output schema: JSON with total_documents, categories{}, and search_engine status. "
            "Error cases: none. Side effects: none. Auth: none."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "upgradeddocs_record_feedback",
        "description": (
            "Record user click feedback for a search result. Call this after the user selects "
            "a search result to improve future search rankings for similar queries. "
            "Input semantics: query (the original search query) and doc_id (the clicked document ID) "
            "are both required. "
            "Output schema: JSON with status, query, and doc_id. "
            "Error cases: missing parameters or unavailable search engine. "
            "Side effects: updates feedback database. Auth: none."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The original search query (e.g. '电池更换')"
                },
                "doc_id": {
                    "type": "string",
                    "description": "The document ID that was clicked (e.g. '37fa81f1d1ae19ae')"
                },
            },
            "required": ["query", "doc_id"],
        },
    },
    {
        "name": "upgradeddocs_ask",
        "description": (
            "Ask a question and get an LLM-generated answer based on knowledge base search results. "
            "Use when you need a natural language answer rather than raw search results. "
            "Input semantics: query is required; top_k optionally limits search results (default 5); "
            "model optionally overrides the LLM model. "
            "Output schema: JSON with answer (natural language), sources (document titles), "
            "query, model, and error (if any). "
            "Error cases: missing query, unavailable search engine, or LLM generation failure. "
            "Side effects: none. Auth: requires OPENAI_API_KEY environment variable or LLM_BASE_URL."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Required question to ask (e.g. 'SRVO-023 报警怎么处理？')"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Maximum search results to use as context. Defaults to 5."
                },
                "model": {
                    "type": "string",
                    "description": "Optional LLM model override (e.g. 'gpt-4o-mini', 'qwen-plus')"
                },
            },
            "required": ["query"],
        },
    },
]


def handle_request(request: dict) -> dict:
    """Handle a JSON-RPC request."""
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {
                    "tools": {},
                    "resources": {},
                },
                "serverInfo": {
                    "name": "upgraded-docs",
                    "version": "1.0.0",
                },
            },
        }

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        handlers = {
            "upgradeddocs_search": handle_search,
            "upgradeddocs_get_document": handle_get_document,
            "upgradeddocs_list_categories": handle_list_categories,
            "upgradeddocs_get_stats": handle_get_stats,
            "upgradeddocs_record_feedback": handle_record_feedback,
            "upgradeddocs_ask": handle_ask,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }

        result = handler(tool_args)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
            },
        }

    elif method == "notifications/initialized":
        return None

    # ── Resources ──
    elif method == "resources/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"resources": handle_resources_list()},
        }

    elif method == "resources/read":
        uri = params.get("uri", "")
        content = handle_resources_read(uri)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "contents": [
                    {"uri": uri, "mimeType": "application/json", "text": json.dumps(content, ensure_ascii=False)}
                ]
            },
        }

    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown method: {method}"},
        }


def main():
    """MCP stdio server loop."""
    sys.stderr.write("upgraded-docs MCP Server started\n")
    sys.stderr.write(f"Search engine: {'available' if HAS_SEARCH else 'unavailable'}\n")
    sys.stderr.write(f"Database: {DB_PATH}\n")

    # Build search index on startup
    if HAS_SEARCH:
        try:
            count = rebuild_search_index()
            sys.stderr.write(f"Search index built: {count} documents\n")
        except Exception as e:
            sys.stderr.write(f"Failed to build search index: {e}\n")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        response = handle_request(request)
        if response:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
