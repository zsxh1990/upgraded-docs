#!/usr/bin/env python3
"""超链接递归下载模块 — 检测链接类型，递归拉取目标内容

V2 新增:
  - 链接类型分类: 飞书文档/飞书表格/普通URL/本地文件
  - 飞书 MCP Pro 集成 (feishu CLI)
  - 飞书 OpenAPI 降级 (环境变量 FEISHU_ACCESS_TOKEN)
  - 通用 HTTP 下载 + HTML 去标签
  - 进度追踪 + 完成度校验门

用法:
  python3 hyperlink_fetcher.py check http://...   # 检测链接类型
  python3 hyperlink_fetcher.py fetch <doc_id>      # 按文档ID拉取所有未处理链接
  python3 hyperlink_fetcher.py validate <doc_id>   # 校验完成度
"""

import os
import re
import sys
import json
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "data" / "robot_knowledge.db"

# ── 链接类型模式 ──────────────────────────────────────────────────

LINK_PATTERNS = {
    "feishu_doc": re.compile(r"https?://[a-z]*\.feishu\.cn/wiki/[a-zA-Z0-9]+"),
    "feishu_sheet": re.compile(r"https?://[a-z]*\.feishu\.cn/sheets/[a-zA-Z0-9]+"),
    "feishu_file": re.compile(r"https?://[a-z]*\.feishu\.cn/file/[a-zA-Z0-9]+"),
    "feishu_bitable": re.compile(r"https?://[a-z]*\.feishu\.cn/base/[a-zA-Z0-9]+"),
    "generic_http": re.compile(r"https?://"),
    "local_file": re.compile(r"file://"),
}


def classify_url(url: str) -> str:
    """判断 URL 类型。"""
    for ltype, pattern in LINK_PATTERNS.items():
        if pattern.match(url):
            return ltype
    # 尝试文件系统路径
    if os.path.exists(url):
        return "local_file"
    return "unknown"


def fetch_url(url: str, link_type: str = "", timeout: int = 15) -> dict:
    """下载目标内容。

    返回: {"content": str, "success": bool, "error": str}
    """
    if not link_type:
        link_type = classify_url(url)

    result = {"content": "", "success": False, "error": ""}

    if link_type.startswith("feishu"):
        content, err = _fetch_feishu(url, link_type)
        result["content"] = content or ""
        result["success"] = bool(content)
        result["error"] = err or ""
        return result

    elif link_type == "generic_http":
        content, err = _fetch_http(url, timeout)
        result["content"] = content or ""
        result["success"] = bool(content)
        result["error"] = err or ""
        return result

    elif link_type == "local_file":
        content, err = _fetch_local(url)
        result["content"] = content or ""
        result["success"] = bool(content)
        result["error"] = err or ""
        return result

    result["error"] = f"不支持的链接类型: {link_type}"
    return result


def _fetch_feishu(url: str, link_type: str) -> tuple:
    """通过飞书 MCP Pro 或 OpenAPI 获取文档内容。"""
    # 方式1: feishu CLI
    try:
        if link_type == "feishu_doc":
            r = subprocess.run(
                ["feishu", "doc", "read", url],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout[:5000], ""
        elif link_type == "feishu_sheet":
            r = subprocess.run(
                ["feishu", "sheet", "export", url, "--format", "csv"],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout[:5000], ""
        elif link_type in ("feishu_bitable", "feishu_file"):
            r = subprocess.run(
                ["feishu", "download", url],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout[:5000], ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # 方式2: 飞书 OpenAPI
    token = os.environ.get("FEISHU_ACCESS_TOKEN", "")
    if token:
        try:
            import requests
            headers = {"Authorization": f"Bearer {token}"}

            if link_type == "feishu_doc":
                m = re.search(r"/wiki/([a-zA-Z0-9]+)", url)
                if m:
                    doc_token = m.group(1)
                    resp = requests.get(
                        f"https://open.feishu.cn/open-apis/docx/v1/documents/{doc_token}/raw_content",
                        headers=headers, timeout=15,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        content = data.get("data", {}).get("content", "")
                        # 去XML标签
                        content = re.sub(r"<[^>]+>", "", content)
                        return content[:5000], ""

            elif link_type == "feishu_sheet":
                m = re.search(r"/sheets/([a-zA-Z0-9]+)", url)
                if m:
                    sheet_token = m.group(1)
                    resp = requests.get(
                        f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{sheet_token}/values/!A:Z",
                        headers=headers, timeout=15,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        values = (data.get("data", {})
                                  .get("valueRange", {})
                                  .get("values", []))
                        lines = [" | ".join(str(c) for c in row) for row in values]
                        return "\n".join(lines)[:5000], ""
        except Exception as e:
            return "", str(e)

    return "", "飞书链接需 MCP Pro 或 FEISHU_ACCESS_TOKEN"


def _fetch_http(url: str, timeout: int) -> tuple:
    """通用 HTTP(S) 下载。"""
    try:
        import requests
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "upgraded-docs/2.0 HyperlinkFetcher",
        }, allow_redirects=True)
        if resp.status_code == 200:
            text = resp.text
            # 去 HTML 标签
            text = re.sub(r"<[^>]+>", " ", text)
            # 压缩空白
            text = re.sub(r"\s+", " ", text).strip()
            # 取前5000字
            return text[:5000], ""
        else:
            return "", f"HTTP {resp.status_code}"
    except Exception as e:
        return "", str(e)


def _fetch_local(path: str) -> tuple:
    """本地文件读取。"""
    try:
        p = Path(path)
        if p.exists():
            content = p.read_text(encoding="utf-8", errors="replace")
            return content[:5000], ""
        return "", f"文件不存在: {path}"
    except Exception as e:
        return "", str(e)


# ── 数据库操作 ────────────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def fetch_doc_links(doc_id: str) -> list[dict]:
    """获取文档的未处理超链接。"""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM hyperlinks WHERE doc_id=? ORDER BY row_idx",
        (doc_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_link_fetch(link_id: str, target_text: str, success: bool, error: str = ""):
    """更新超链接抓取结果。"""
    conn = _get_db()
    conn.execute("""
        UPDATE hyperlinks
        SET target_text=?, fetched=?, fetch_error=?
        WHERE id=?
    """, (target_text[:5000], 1 if success else 0, error[:200], link_id))
    conn.commit()
    conn.close()


# ── 批量处理 ──────────────────────────────────────────────────────

def fetch_pending_links(max_links: int = 20) -> dict:
    """批量处理所有未抓取的超链接。"""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM hyperlinks WHERE fetched=0 LIMIT ?",
        (max_links,)
    ).fetchall()
    conn.close()

    results = {"total": len(rows), "success": 0, "failed": 0, "skipped": 0}
    for row in rows:
        link = dict(row)
        result = fetch_url(link["url"], link.get("link_type", ""))
        update_link_fetch(link["id"], result["content"],
                          result["success"], result["error"])
        if result["success"]:
            results["success"] += 1
            print(f"  ✅ {link['url'][:60]}...")
        else:
            results["failed"] += 1
            print(f"  ❌ {link['url'][:60]}... ({result['error'][:40]})")

    return results


def validate_doc_links(doc_id: str) -> dict:
    """校验文档超链接完成度。"""
    links = fetch_doc_links(doc_id)
    total = len(links)
    fetched = sum(1 for l in links if l.get("fetched"))
    failed = sum(1 for l in links if not l.get("fetched") and l.get("fetch_error"))
    pending = total - fetched - failed

    return {
        "doc_id": doc_id,
        "total": total,
        "fetched": fetched,
        "failed": failed,
        "pending": pending,
        "completeness": f"{fetched}/{total}" if total else "N/A",
    }


# ── CLI ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == "check":
        url = sys.argv[2] if len(sys.argv) > 2 else input("URL: ")
        ltype = classify_url(url)
        print(f"URL:   {url}")
        print(f"类型:  {ltype}")
        if ltype != "unknown":
            result = fetch_url(url, ltype)
            if result["success"]:
                print(f"内容:  {result['content'][:200]}...")
            else:
                print(f"错误:  {result['error']}")

    elif command == "fetch":
        max_links = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        results = fetch_pending_links(max_links=max_links)
        print(f"\n📊 批量处理完成: {results['success']}成功 / {results['failed']}失败 / {results['skipped']}跳过")

    elif command == "validate":
        doc_id = sys.argv[2] if len(sys.argv) > 2 else ""
        if not doc_id:
            print("需要 doc_id")
            sys.exit(1)
        stats = validate_doc_links(doc_id)
        print(f"文档 {stats['doc_id']}:")
        print(f"  总链接: {stats['total']}")
        print(f"  已处理: {stats['fetched']}")
        print(f"  失败:   {stats['failed']}")
        print(f"  待处理: {stats['pending']}")
        print(f"  完成度: {stats['completeness']}")

    elif command == "stats":
        conn = _get_db()
        row = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(fetched) as fetched,
                   SUM(CASE WHEN fetched=0 AND fetch_error!='' THEN 1 ELSE 0 END) as failed
            FROM hyperlinks
        """).fetchone()
        conn.close()
        print(f"超链接统计:")
        print(f"  总数: {row['total']}")
        print(f"  已抓取: {row['fetched']}")
        print(f"  失败: {row['failed']}")
        print(f"  待处理: {row['total'] - row['fetched'] - row['failed']}")

    else:
        print(f"未知命令: {command}")
        sys.exit(1)
