"""Obsidian 深度绑定模块

通过 obsidian-local-rest-api 插件（https://github.com/coddingtonbear/obsidian-local-rest-api）
连接本地 Obsidian 仓库，实现：

1. 自动索引 Obsidian 笔记到知识库搜索
2. [[wikilink]] 双向链接解析为文档关联
3. 文件变更监听（轮询）+ 自动重建索引
4. 知识库标注/评论回写到 Obsidian

使用前提：
  Obsidian 中安装 obsidian-local-rest-api 插件
  设置 → 社区插件 → 浏览 → 搜索 "Local REST API" → 安装启用
  默认端口: 27124
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

logger = logging.getLogger(__name__)

# Obsidian Local REST API 默认配置
OBSIDIAN_API_BASE = "http://localhost:27124"
VAULT_DIR = ""  # 由 configure() 设置


def configure(api_base: str = "", vault_dir: str = ""):
    """配置 Obsidian 连接参数。"""
    global OBSIDIAN_API_BASE, VAULT_DIR
    if api_base:
        OBSIDIAN_API_BASE = api_base.rstrip("/")
    if vault_dir:
        VAULT_DIR = vault_dir


# ── API 调用 ──

def _api_get(path: str) -> dict | None:
    """调用 Obsidian Local REST API GET 端点。"""
    url = f"{OBSIDIAN_API_BASE}{path}"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except URLError as e:
        logger.warning(f"Obsidian API 不可达 ({url}): {e}")
        return None
    except json.JSONDecodeError:
        logger.warning(f"Obsidian API 返回非 JSON ({url})")
        return None


def _api_put(path: str, data: dict) -> bool:
    """写入内容到 Obsidian。"""
    url = f"{OBSIDIAN_API_BASE}{path}"
    try:
        body = json.dumps(data).encode()
        req = Request(url, data=body, method="PUT",
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=10):
            return True
    except URLError as e:
        logger.warning(f"Obsidian API 写入失败: {e}")
        return False


# ── 健康检查 ──

def is_available() -> bool:
    """检测 Obsidian Local REST API 是否正在运行。"""
    result = _api_get("/")
    return result is not None


# ── 笔记列表 ──

def list_notes(ext: str = ".md") -> list[dict]:
    """获取 Obsidian 仓库中所有 Markdown 笔记列表。

    返回: [{"path": "dir/note.md", "name": "note"}, ...]
    """
    result = _api_get("/vault/")
    if not result:
        return []
    files = []
    for item in result if isinstance(result, list) else result.get("files", []):
        path = item.get("path", item.get("name", ""))
        if path.endswith(ext):
            files.append({
                "path": path,
                "name": Path(path).stem,
            })
    return files


def read_note(path: str) -> str | None:
    """读取 Obsidian 笔记内容。"""
    # URL encode 路径
    encoded = path.replace("/", "%2F")
    result = _api_get(f"/vault/{encoded}")
    if result is None:
        return None
    # 响应可能是字符串或 {content: ...}
    if isinstance(result, str):
        return result
    return result.get("content", result.get("data", ""))


# ── Wikilink 解析 ──

WIKILINK_RE = re.compile(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]')


def extract_wikilinks(content: str) -> list[str]:
    """从内容中提取所有 [[wikilink]] 目标。"""
    return [m.group(1).strip() for m in WIKILINK_RE.finditer(content)]


def resolve_wikilink(target: str, notes: list[dict]) -> str | None:
    """将 wikilink 目标解析为实际笔记路径。

    支持: [[目标文件名]] （不含 .md 后缀自动补全）
    """
    target = target.strip()
    # 直接匹配
    for n in notes:
        if n["name"] == target or n["path"] == target:
            return n["path"]
    # 补全 .md
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
        content = read_note(note["path"])
        if not content or len(content.strip()) < 10:
            continue

        # 提取标题（一级标题或文件名）
        title_m = re.search(r"^#\s+(.+)", content, re.MULTILINE)
        title = title_m.group(1).strip() if title_m else note["name"]
        # 提取 wikilinks
        wikilinks = extract_wikilinks(content)
        # 提取 tags（Obsidian tags: #tag）
        tags = re.findall(r'(?<!\w)#([\w\-/]+)', content)

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


# ── 写入标注到 Obsidian ──

def write_annotation(note_path: str, annotation: str) -> bool:
    """向指定 Obsidian 笔记追加标注/评论。"""
    existing = read_note(note_path)
    if existing is None:
        return False
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_content = f"{existing}\n\n> **知识库标注** ({timestamp})\n> {annotation}\n"
    return _api_put(f"/vault/{note_path.replace('/', '%2F')}",
                    {"content": new_content})


def sync_knowledge_graph_to_obsidian(graph_data: dict) -> dict:
    """将知识图谱数据同步回 Obsidian 笔记。

    将矛盾、实体、声明写入对应笔记的 YAML frontmatter。
    """
    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])

    stats = {
        "contradictions_written": 0,
        "entities_written": 0,
        "claims_written": 0,
        "errors": [],
    }

    # 1. 提取矛盾边，写入对应文档
    contradictions = [e for e in edges if e.get("type") == "contradicts"]
    for c in contradictions:
        source_id = c.get("source", "")
        target_id = c.get("target", "")

        # 找到对应的文档节点
        source_node = next((n for n in nodes if n["id"] == source_id), None)
        target_node = next((n for n in nodes if n["id"] == target_id), None)

        if not source_node or not target_node:
            continue

        # 生成标注内容
        desc = c.get("description", "参数/流程冲突")
        meta = c.get("contradictionMeta", {})
        annotation = (
            f"**矛盾检测**\n"
            f"- 对象: {target_node.get('name', target_id)}\n"
            f"- 描述: {desc}\n"
        )
        if meta.get("param"):
            annotation += f"- 参数: {meta['param']}\n"
        if meta.get("value1") and meta.get("value2"):
            annotation += f"- 本文件值: {meta['value1']}\n- 对比文件值: {meta['value2']}\n"

        # 查找对应 Obsidian 笔记路径
        source_doc = source_node.get("knowledgeMeta", {}).get("sourceDoc", "")
        if source_doc:
            note_path = _find_note_path(source_doc)
            if note_path:
                ok = write_annotation(note_path, annotation)
                if ok:
                    stats["contradictions_written"] += 1
                else:
                    stats["errors"].append(f"写入失败: {note_path}")

    # 2. 提取实体，写入源文档
    entity_nodes = [n for n in nodes if n.get("type") == "entity"]
    for entity in entity_nodes:
        entity_meta = entity.get("entityMeta", {})
        source_doc = entity_meta.get("sourceDoc", "")
        if not source_doc:
            continue

        name = entity.get("name", "")
        summary = entity.get("summary", "")
        entity_type = entity_meta.get("entityType", "unknown")

        annotation = (
            f"**实体识别**\n"
            f"- 名称: {name}\n"
            f"- 类型: {entity_type}\n"
            f"- 描述: {summary}\n"
        )

        note_path = _find_note_path(source_doc)
        if note_path:
            ok = write_annotation(note_path, annotation)
            if ok:
                stats["entities_written"] += 1
            else:
                stats["errors"].append(f"写入失败: {note_path}")

    # 3. 提取声明，写入源文档
    claim_nodes = [n for n in nodes if n.get("type") == "claim"]
    for claim in claim_nodes:
        claim_meta = claim.get("claimMeta", {})
        source_doc = claim_meta.get("sourceDoc", "")
        if not source_doc:
            continue

        title = claim.get("name", "")
        content = claim.get("summary", "")
        category = claim_meta.get("category", "unknown")

        annotation = (
            f"**关键声明**\n"
            f"- 标题: {title}\n"
            f"- 类别: {category}\n"
            f"- 内容: {content[:200]}\n"
        )

        note_path = _find_note_path(source_doc)
        if note_path:
            ok = write_annotation(note_path, annotation)
            if ok:
                stats["claims_written"] += 1
            else:
                stats["errors"].append(f"写入失败: {note_path}")

    return stats


def _find_note_path(doc_name: str) -> str | None:
    """根据文档名查找 Obsidian 笔记路径。"""
    notes = list_notes()
    # 尝试精确匹配
    for n in notes:
        if n["name"] == doc_name or doc_name in n["name"]:
            return n["path"]
    # 尝试模糊匹配
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
        """扫描 Obsidian vault 文件 mtime。"""
        if not VAULT_DIR:
            return {}
        vault = Path(VAULT_DIR)
        if not vault.exists():
            return {}
        mtimes = {}
        for f in vault.rglob("*.md"):
            mtimes[str(f.relative_to(vault))] = f.stat().st_mtime
        return mtimes

    def check_changes(self) -> list[str]:
        """返回自上次检查后变更的文件列表。"""
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
        """开始轮询（阻塞）。"""
        self._known_mtimes = self._scan_mtimes()
        self._running = True
        logger.info(f"ObsidianWatcher 已启动 (interval={self.interval}s)")
        while self._running:
            time.sleep(self.interval)
            changed = self.check_changes()
            if changed and self.callback:
                self.callback(changed)

    def stop(self):
        self._running = False


# ── 命令行入口 ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    if is_available():
        notes = list_notes()
        print(f"Obsidian 连接成功, {len(notes)} 篇笔记")
        for n in notes[:5]:
            print(f"  - {n['path']}")
    else:
        print("❌ Obsidian Local REST API 不可达")
        print("   请确保: 1) Obsidian 已启动 2) 已安装 Local REST API 插件")
