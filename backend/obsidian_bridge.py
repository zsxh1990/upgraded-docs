"""Obsidian 深度绑定模块

双模式运行：
  1. 本地直读（默认）— 自动发现 vault 目录，直接读 .md 文件，零配置
  2. REST API 增强 — 装了 obsidian-local-rest-api 插件后额外支持回写标注

自动发现逻辑：
  1. 环境变量 OBSIDIAN_VAULT
  2. 常见位置扫描（~/Desktop, ~/Documents, ~/iCloud, ~/Obsidian 等）
  3. REST API /vault/ 端点（需插件）

环境变量：
  OBSIDIAN_VAULT    — Vault 目录路径（跳过自动发现）
  OBSIDIAN_API_BASE — REST API 地址（默认 http://localhost:27124）
  OBSIDIAN_API_KEY  — REST API 密钥
"""

import os
import re
import time
import json
import hashlib
import logging
import sqlite3
from pathlib import Path
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError
from urllib.parse import quote as urlquote

logger = logging.getLogger(__name__)

# ── 配置 ──

OBSIDIAN_API_BASE = os.environ.get("OBSIDIAN_API_BASE", "http://localhost:27124")
OBSIDIAN_API_KEY = os.environ.get("OBSIDIAN_API_KEY", "")
VAULT_DIR = os.environ.get("OBSIDIAN_VAULT", "")

# 笔记列表缓存
_notes_cache: list[dict] | None = None
_notes_cache_time: float = 0.0
_NOTES_CACHE_TTL = 30  # 30秒

# 运行模式
_local_mode = False  # True = 直接读文件, False = REST API
_rest_available = False  # REST API 是否可用


def configure(api_base: str = "", vault_dir: str = "", api_key: str = ""):
    """配置 Obsidian 连接参数。"""
    global OBSIDIAN_API_BASE, VAULT_DIR, OBSIDIAN_API_KEY
    if api_base:
        OBSIDIAN_API_BASE = api_base.rstrip("/")
    if vault_dir:
        VAULT_DIR = vault_dir
    if api_key:
        OBSIDIAN_API_KEY = api_key


# ── Vault 自动发现 ──

def _discover_vault() -> str:
    """自动发现 Obsidian vault 目录。

    扫描逻辑：
    1. 环境变量 OBSIDIAN_VAULT
    2. 常见位置下含 .obsidian 的目录
    """
    global VAULT_DIR

    # 已配置
    if VAULT_DIR and Path(VAULT_DIR, ".obsidian").exists():
        return VAULT_DIR

    # 扫描常见位置
    home = Path.home()
    search_dirs = [
        home / "Desktop",
        home / "Documents",
        home / "Obsidian",
        home / "iCloud Drive" / "Obsidian",
        home / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents",
    ]

    for base in search_dirs:
        if not base.exists():
            continue
        # 直接检查 base 本身
        if (base / ".obsidian").exists():
            VAULT_DIR = str(base)
            logger.info(f"自动发现 vault: {VAULT_DIR}")
            return VAULT_DIR
        # 检查一级子目录
        for child in sorted(base.iterdir()):
            if child.is_dir() and not child.name.startswith(".") and (child / ".obsidian").exists():
                VAULT_DIR = str(child)
                logger.info(f"自动发现 vault: {VAULT_DIR}")
                return VAULT_DIR

    return ""


def _ensure_vault() -> str:
    """确保 vault 目录已发现，返回路径或空串。"""
    if VAULT_DIR:
        return VAULT_DIR
    return _discover_vault()


# ── REST API 调用（可选增强） ──

def _auth_headers(content_type: str = "application/json") -> dict:
    headers = {"Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    if OBSIDIAN_API_KEY:
        headers["Authorization"] = f"Bearer {OBSIDIAN_API_KEY}"
    return headers


def _api_get(path: str) -> dict | None:
    url = f"{OBSIDIAN_API_BASE}{path}"
    try:
        req = Request(url, headers=_auth_headers())
        with urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _api_put(path: str, data: dict) -> bool:
    url = f"{OBSIDIAN_API_BASE}{path}"
    try:
        body = json.dumps(data).encode()
        req = Request(url, data=body, method="PUT", headers=_auth_headers())
        with urlopen(req, timeout=10):
            return True
    except Exception as e:
        logger.warning(f"Obsidian API 写入失败: {e}")
        return False


def _check_rest_api() -> bool:
    """检测 REST API 是否可用。"""
    global _rest_available
    _rest_available = _api_get("/") is not None
    return _rest_available


# ── 健康检查 ──

def is_available() -> bool:
    """检测是否有可用的 Obsidian 连接（本地文件或 REST API）。"""
    vault = _ensure_vault()
    if vault:
        return True
    return _check_rest_api()


def status() -> dict:
    """返回连接状态详情。"""
    vault = _ensure_vault()
    rest_ok = _check_rest_api()
    return {
        "vault_dir": vault,
        "local_mode": bool(vault),
        "rest_api": rest_ok,
        "write_back": rest_ok,  # 回写需要 REST API
    }


# ── 笔记列表 ──

def list_notes(ext: str = ".md", use_cache: bool = True) -> list[dict]:
    """获取 Obsidian 仓库中所有 Markdown 笔记列表。

    优先本地直读，无 vault 时回退 REST API。
    返回: [{"path": "dir/note.md", "name": "note"}, ...]
    """
    global _notes_cache, _notes_cache_time, _local_mode

    if use_cache and _notes_cache is not None:
        if time.time() - _notes_cache_time < _NOTES_CACHE_TTL:
            return _notes_cache

    # 优先本地直读
    vault = _ensure_vault()
    if vault:
        files = _local_list_notes(vault, ext)
        if files:
            _local_mode = True
            _notes_cache = files
            _notes_cache_time = time.time()
            return files

    # 回退 REST API
    files = _rest_list_notes(ext)
    if files:
        _local_mode = False
        _notes_cache = files
        _notes_cache_time = time.time()

    return files


def _local_list_notes(vault: str, ext: str = ".md") -> list[dict]:
    """从本地文件系统扫描笔记。"""
    vault_path = Path(vault)
    files = []
    for f in vault_path.rglob(f"*{ext}"):
        # 跳过 .obsidian 目录和隐藏文件
        rel = f.relative_to(vault_path)
        if any(part.startswith(".") for part in rel.parts):
            continue
        files.append({
            "path": str(rel),
            "name": f.stem,
        })
    return sorted(files, key=lambda x: x["path"])


def _rest_list_notes(ext: str = ".md") -> list[dict]:
    """通过 REST API 获取笔记列表。"""
    result = _api_get("/vault/")
    if not result:
        return []
    files = []
    raw_files = result if isinstance(result, list) else result.get("files", [])
    for item in raw_files:
        if isinstance(item, str):
            path = item
        elif isinstance(item, dict):
            path = item.get("path", item.get("name", ""))
        else:
            continue
        if path.endswith(ext):
            files.append({"path": path, "name": Path(path).stem})
    return files


def invalidate_notes_cache():
    global _notes_cache, _notes_cache_time
    _notes_cache = None
    _notes_cache_time = 0.0


# ── 笔记读取 ──

def read_note(path: str) -> str | None:
    """读取 Obsidian 笔记内容。优先本地文件。"""
    vault = _ensure_vault()
    if vault:
        content = _local_read_note(vault, path)
        if content is not None:
            return content

    # 回退 REST API
    return _rest_read_note(path)


def _local_read_note(vault: str, path: str) -> str | None:
    """从本地文件系统读取笔记。"""
    full_path = Path(vault) / path
    if not full_path.exists():
        # 尝试带 .md 后缀
        if not path.endswith(".md"):
            full_path = Path(vault) / (path + ".md")
    if not full_path.exists():
        return None
    try:
        return full_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"读取笔记失败 ({full_path}): {e}")
        return None


def _rest_read_note(path: str) -> str | None:
    """通过 REST API 读取笔记。"""
    encoded = urlquote(path, safe="")
    result = _api_get(f"/vault/{encoded}")
    if result is None:
        return None
    if isinstance(result, str):
        return result
    return result.get("content", result.get("data", ""))


# ── Frontmatter 处理 ──

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


def strip_frontmatter(content: str) -> str:
    """移除 YAML frontmatter，只返回正文。"""
    return _FRONTMATTER_RE.sub("", content, count=1)


# ── Wikilink 解析 ──

WIKILINK_RE = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]')


def extract_wikilinks(content: str) -> list[str]:
    """从内容中提取所有 [[wikilink]] 目标。"""
    return [m.group(1).strip() for m in WIKILINK_RE.finditer(content)]


def resolve_wikilink(target: str, notes: list[dict]) -> str | None:
    """将 wikilink 目标解析为实际笔记路径。"""
    target = target.strip()
    for n in notes:
        if n["name"] == target or n["path"] == target:
            return n["path"]
    if not target.endswith(".md"):
        target_md = target + ".md"
        for n in notes:
            if n["name"] == target_md or n["path"] == target_md:
                return n["path"]
    return None


# ── 索引构建 ──

def build_obsidian_index(notes: list[dict] | None = None) -> list[dict]:
    """构建 Obsidian 笔记的搜索索引。

    返回: [{"id": str, "title": str, "content": str, "category": str,
            "filename": str, "wikilinks": [str], "source": "obsidian"}, ...]
    """
    if notes is None:
        notes = list_notes()

    docs = []
    for note in notes:
        raw_content = read_note(note["path"])
        if not raw_content or len(raw_content.strip()) < 10:
            continue

        content = strip_frontmatter(raw_content)

        title_m = re.search(r"^#\s+(.+)", content, re.MULTILINE)
        title = title_m.group(1).strip() if title_m else note["name"]
        wikilinks = extract_wikilinks(content)
        tags = re.findall(r'(?<!\w)#([\w\-/]+)', raw_content)

        doc_id = hashlib.md5(f"obsidian:{note['path']}".encode()).hexdigest()[:16]
        docs.append({
            "id": doc_id,
            "title": title,
            "content": content,
            "category": "Obsidian",
            "filename": note["path"],
            "wikilinks": wikilinks,
            "tags": tags,
            "source": "obsidian",
        })
    return docs


# ── 写入标注（需 REST API） ──

def write_annotation(note_path: str, annotation: str) -> bool:
    """向指定 Obsidian 笔记追加标注/评论。需要 REST API 插件。"""
    if not _rest_available and not _check_rest_api():
        logger.warning("回写标注需要 obsidian-local-rest-api 插件")
        return False

    existing = read_note(note_path)
    if existing is None:
        return False
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_content = f"{existing}\n\n> **知识库标注** ({timestamp})\n> {annotation}\n"
    return _api_put(f"/vault/{urlquote(note_path, safe='')}",
                    {"content": new_content})


def sync_knowledge_graph_to_obsidian(graph_data: dict) -> dict:
    """将知识图谱数据同步回 Obsidian 笔记。需要 REST API 插件。"""
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    stats = {
        "contradictions_written": 0,
        "entities_written": 0,
        "claims_written": 0,
        "errors": [],
    }

    if not _rest_available and not _check_rest_api():
        stats["errors"].append("回写需要安装 obsidian-local-rest-api 插件")
        return stats

    notes = list_notes()

    # 1. 矛盾边
    for c in [e for e in edges if e.get("type") == "contradicts"]:
        source_node = next((n for n in nodes if n["id"] == c.get("source")), None)
        target_node = next((n for n in nodes if n["id"] == c.get("target")), None)
        if not source_node or not target_node:
            continue

        desc = c.get("description", "参数/流程冲突")
        meta = c.get("contradictionMeta", {})
        annotation = f"**矛盾检测**\n- 对象: {target_node.get('name', c.get('target'))}\n- 描述: {desc}\n"
        if meta.get("param"):
            annotation += f"- 参数: {meta['param']}\n"
        if meta.get("value1") and meta.get("value2"):
            annotation += f"- 本文件值: {meta['value1']}\n- 对比文件值: {meta['value2']}\n"

        source_doc = source_node.get("knowledgeMeta", {}).get("sourceDoc", "")
        if source_doc:
            note_path = _find_note_path(source_doc, notes)
            if note_path and write_annotation(note_path, annotation):
                stats["contradictions_written"] += 1
            elif note_path:
                stats["errors"].append(f"写入失败: {note_path}")

    # 2. 实体
    for entity in [n for n in nodes if n.get("type") == "entity"]:
        source_doc = entity.get("entityMeta", {}).get("sourceDoc", "")
        if not source_doc:
            continue
        annotation = (
            f"**实体识别**\n"
            f"- 名称: {entity.get('name', '')}\n"
            f"- 类型: {entity.get('entityMeta', {}).get('entityType', 'unknown')}\n"
            f"- 描述: {entity.get('summary', '')}\n"
        )
        note_path = _find_note_path(source_doc, notes)
        if note_path and write_annotation(note_path, annotation):
            stats["entities_written"] += 1

    # 3. 声明
    for claim in [n for n in nodes if n.get("type") == "claim"]:
        source_doc = claim.get("claimMeta", {}).get("sourceDoc", "")
        if not source_doc:
            continue
        annotation = (
            f"**关键声明**\n"
            f"- 标题: {claim.get('name', '')}\n"
            f"- 类别: {claim.get('claimMeta', {}).get('category', 'unknown')}\n"
            f"- 内容: {claim.get('summary', '')[:200]}\n"
        )
        note_path = _find_note_path(source_doc, notes)
        if note_path and write_annotation(note_path, annotation):
            stats["claims_written"] += 1

    return stats


def _find_note_path(doc_name: str, notes: list[dict] | None = None) -> str | None:
    """根据文档名查找 Obsidian 笔记路径。"""
    if notes is None:
        notes = list_notes()
    for n in notes:
        if n["name"] == doc_name or doc_name in n["name"]:
            return n["path"]
    for n in notes:
        if doc_name.lower() in n["name"].lower():
            return n["path"]
    return None


# ── 监控（轮询模式） ──

class ObsidianWatcher:
    """监听 Obsidian 仓库文件变更，自动触发索引重建。"""

    def __init__(self, callback=None, interval: int = 60):
        self.callback = callback
        self.interval = interval
        self._known_mtimes: dict[str, float] = {}
        self._running = False

    def _scan_mtimes(self) -> dict[str, float]:
        vault = _ensure_vault()
        if not vault:
            return {}
        vault_path = Path(vault)
        if not vault_path.exists():
            return {}
        mtimes = {}
        for f in vault_path.rglob("*.md"):
            if any(part.startswith(".") for part in f.relative_to(vault_path).parts):
                continue
            mtimes[str(f.relative_to(vault_path))] = f.stat().st_mtime
        return mtimes

    def check_changes(self) -> list[str]:
        current = self._scan_mtimes()
        changed = []
        for path, mtime in current.items():
            if path not in self._known_mtimes:
                changed.append(path)
            elif abs(mtime - self._known_mtimes[path]) > 0.5:
                changed.append(path)
        self._known_mtimes = current
        return changed

    def start(self):
        self._known_mtimes = self._scan_mtimes()
        self._running = True
        logger.info(f"ObsidianWatcher 已启动 (interval={self.interval}s, vault={VAULT_DIR})")
        while self._running:
            time.sleep(self.interval)
            changed = self.check_changes()
            if changed and self.callback:
                self.callback(changed)
                invalidate_notes_cache()

    def stop(self):
        self._running = False


# ── 命令行入口 ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    s = status()
    print(f"Vault: {s['vault_dir'] or '未找到'}")
    print(f"本地模式: {'是' if s['local_mode'] else '否'}")
    print(f"REST API: {'可用' if s['rest_api'] else '不可用'}")
    print(f"回写标注: {'支持' if s['write_back'] else '不支持（需安装插件）'}")

    if s["local_mode"] or s["rest_api"]:
        notes = list_notes()
        print(f"\n笔记数: {len(notes)}")
        for n in notes[:5]:
            print(f"  - {n['path']}")
    else:
        print("\n未找到 Obsidian vault，也无 REST API 连接")
        print("提示: 设置 OBSIDIAN_VAULT 环境变量指定 vault 路径")
