#!/usr/bin/env python3
"""文档导入脚本 V2 — Excel 结构感知 + 超链接递归 + 冲突检测增强

V2 改进:
  1. Excel 结构感知分类: 表格型/参数型/报表型/混合型，每类走不同提取路径
  2. 超链接递归下载: 检测单元格超链接，分类拉取目标内容
  3. 格式分叉: 参数型→JSON行, 表格型→DataFrame, 报表型→键值对, 文本型→段落
  4. 导入时规则层冲突检测: 同名参数数值直接比对（不依赖 LLM）

用法:
  python3 import_docs.py docs/                    # 导入整个目录
  python3 import_docs.py docs/spec.md             # 导入单个文件
  python3 import_docs.py docs/ --category=规范    # 指定分类
  python3 import_docs.py docs/ --no-semantic      # 跳过语义索引重建
"""

import os
import sys
import re
import json
import hashlib
import sqlite3
from pathlib import Path
from datetime import datetime
from collections import defaultdict

BASE = Path(__file__).resolve().parent.parent
DB_PATH = BASE / "data" / "robot_knowledge.db"
CONFLICTS_DIR = BASE / "data" / "conflicts"

SUPPORTED = {".md", ".txt", ".docx", ".xlsx", ".pptx"}

# ── 辅助: 数据库初始化（结构化数据表） ─────────────────────────────

def _init_structured_tables():
    """确保结构化数据表存在（参数表 + 配置表 + 超链接表）。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS parameters (
            id         TEXT PRIMARY KEY,
            doc_id     TEXT REFERENCES documents(id),
            param_name TEXT NOT NULL,
            value      TEXT,
            unit       TEXT DEFAULT '',
            sheet_name TEXT DEFAULT '',
            row_idx    INTEGER DEFAULT 0,
            normalized TEXT DEFAULT '',
            source     TEXT DEFAULT 'excel',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hyperlinks (
            id           TEXT PRIMARY KEY,
            doc_id       TEXT REFERENCES documents(id),
            sheet_name   TEXT DEFAULT '',
            row_idx      INTEGER DEFAULT 0,
            col_idx      INTEGER DEFAULT 0,
            cell_text    TEXT DEFAULT '',
            url          TEXT DEFAULT '',
            link_type    TEXT DEFAULT 'unknown',
            target_text  TEXT DEFAULT '',
            fetched      INTEGER DEFAULT 0,
            fetch_error  TEXT DEFAULT '',
            created_at   TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_param_name ON parameters(normalized)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_param_doc ON parameters(doc_id)
    """)
    conn.commit()
    conn.close()


# ── Excel 结构感知分类 ────────────────────────────────────────────

def classify_sheet(ws, sample_rows: int = 5) -> str:
    """基于前 N 行+列结构判断 Sheet 类型。

    返回: "tabular" / "spec" / "report" / "mixed"
    """
    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= sample_rows:
            break
        rows.append([str(c) if c is not None else "" for c in row])

    if not rows:
        return "text"

    ncols = max(len(r) for r in rows)
    for r in rows:
        while len(r) < ncols:
            r.append("")

    col_features = []
    for col_i in range(ncols):
        col_vals = [r[col_i] for r in rows]
        numeric_count = 0
        empty_count = 0
        for v in col_vals:
            if not v.strip():
                empty_count += 1
            else:
                try:
                    float(v.replace(",", "").replace(" ", ""))
                    numeric_count += 1
                except ValueError:
                    pass
        col_features.append({
            "numeric_ratio": numeric_count / max(len(rows), 1),
            "empty_ratio": empty_count / max(len(rows), 1),
        })

    total_numeric_ratio = sum(f["numeric_ratio"] for f in col_features) / max(ncols, 1)
    total_empty_ratio = sum(f["empty_ratio"] for f in col_features) / max(ncols, 1)

    # 前三行中短文本标签占比
    header_likely = sum(
        1 for r in rows[:3] for v in r
        if v.strip() and len(v.strip()) < 20
        and not v.strip().replace(".", "").replace("-", "").isdigit()
    ) / max(sum(1 for r in rows[:3] for v in r if v.strip()), 1)

    if total_numeric_ratio > 0.4 and total_empty_ratio < 0.3:
        return "tabular"
    elif header_likely > 0.6 and total_numeric_ratio < 0.3 and ncols <= 3:
        return "spec"
    elif total_empty_ratio > 0.5 and ncols > 2:
        return "report"
    else:
        return "mixed"


# ── 各类型提取 ────────────────────────────────────────────────────

def extract_tabular(ws, sheet_name: str) -> list[dict]:
    """表格型 → 第一行做表头，每行一个 dict。"""
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = [str(c).strip() if c is not None else f"col_{i}"
                  for i, c in enumerate(next(rows_iter))]
    except StopIteration:
        return []

    header = [h if h else f"col_{i}" for i, h in enumerate(header)]
    seen = defaultdict(int)
    for i, h in enumerate(header):
        seen[h] += 1
        if seen[h] > 1:
            header[i] = f"{h}_{seen[h]}"

    records = []
    for row in rows_iter:
        cells = [str(c).strip() if c is not None else "" for c in row]
        while len(cells) < len(header):
            cells.append("")
        if all(not c for c in cells):
            continue
        record = dict(zip(header, cells))
        record["_sheet"] = sheet_name
        records.append(record)
    return records


def extract_spec(ws, sheet_name: str) -> list[dict]:
    """参数型 → {param, value, unit, note} 键值对。"""
    specs = []
    for row in ws.iter_rows(values_only=True):
        cells = [str(c).strip() if c is not None else "" for c in row]
        if not cells or not cells[0]:
            continue
        entry = {
            "param": cells[0],
            "value": cells[1] if len(cells) > 1 else "",
            "unit": cells[2] if len(cells) > 2 else "",
            "note": cells[3] if len(cells) > 3 else "",
            "_sheet": sheet_name,
        }
        specs.append(entry)
    return specs


def extract_report(ws, sheet_name: str) -> list[dict]:
    """报表型 → 按空行分割区域，每个区域组键值对。"""
    regions = []
    current = {}
    for row in ws.iter_rows(values_only=True):
        cells = [str(c).strip() if c is not None else "" for c in row]
        if all(not c for c in cells):
            if current:
                regions.append(current)
                current = {}
            continue
        if len(cells) >= 2 and cells[0] and cells[1]:
            current[cells[0]] = cells[1]
            if len(cells) > 2 and cells[2]:
                current[f"{cells[0]}_unit"] = cells[2]
    if current:
        regions.append(current)
    return regions


def extract_mixed(ws, sheet_name: str) -> dict:
    """混合型 → 表格段落分离。"""
    paragraphs = []
    table_regions = []
    current_table = []

    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, values_only=True):
        texts = [str(c).strip() if c is not None else "" for c in row]
        filled = sum(1 for t in texts if t)
        if filled >= 3:
            current_table.append(texts)
        else:
            if current_table:
                table_regions.append(current_table)
                current_table = []
            line = " | ".join(t for t in texts if t)
            if line:
                paragraphs.append(line)

    if current_table:
        table_regions.append(current_table)

    return {
        "paragraphs": paragraphs,
        "tables": table_regions,
        "_sheet": sheet_name,
    }


# ── 超链接检测 + 递归下载 ──────────────────────────────────────────

_LINK_PATTERNS = {
    "feishu_doc": re.compile(r"https?://[a-z]*\.feishu\.cn/wiki/"),
    "feishu_sheet": re.compile(r"https?://[a-z]*\.feishu\.cn/sheets/"),
    "feishu_file": re.compile(r"https?://[a-z]*\.feishu\.cn/file/"),
    "generic_url": re.compile(r"https?://"),
}


def classify_link(url: str) -> str:
    for ltype, pattern in _LINK_PATTERNS.items():
        if pattern.match(url):
            return ltype
    return "unknown"


def detect_hyperlinks(ws, sheet_name: str) -> list[dict]:
    """检测 Sheet 中的所有超链接。"""
    links = []
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
        for cell in row:
            if cell.hyperlink and cell.hyperlink.target:
                url = cell.hyperlink.target.strip()
                text = str(cell.value).strip() if cell.value else ""
                links.append({
                    "row": cell.row,
                    "col": cell.column,
                    "cell_text": text,
                    "url": url,
                    "link_type": classify_link(url),
                    "_sheet": sheet_name,
                })
    return links


def fetch_link_target(link: dict, timeout: int = 15) -> str:
    """递归下载超链接目标内容。"""
    url = link["url"]
    ltype = link["link_type"]

    if ltype.startswith("feishu"):
        result = _fetch_feishu_link(url, ltype)
        return result if result else f"[飞书链接需 MCP Pro 手动处理: {url}]"

    elif ltype == "generic_url":
        try:
            import requests
            resp = requests.get(url, timeout=timeout, headers={
                "User-Agent": "upgraded-docs/2.0",
            })
            if resp.status_code == 200:
                text = resp.text
                text = re.sub(r"<[^>]+>", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                return text[:5000]
            else:
                return f"[HTTP {resp.status_code}]"
        except Exception as e:
            return f"[下载失败: {e}]"

    return "[未支持的链接类型]"


def _fetch_feishu_link(url: str, link_type: str) -> str:
    """尝试通过飞书 MCP Pro 或 OpenAPI 获取文档内容。"""
    import subprocess
    try:
        if link_type == "feishu_doc":
            result = subprocess.run(
                ["feishu", "doc", "read", url],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return result.stdout[:5000]
        elif link_type == "feishu_sheet":
            result = subprocess.run(
                ["feishu", "sheet", "export", url, "--format", "csv"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return result.stdout[:5000]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    feishu_token = os.environ.get("FEISHU_ACCESS_TOKEN", "")
    if feishu_token:
        try:
            import requests
            headers = {"Authorization": f"Bearer {feishu_token}"}
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
                        return data.get("data", {}).get("content", "")[:5000]
        except Exception:
            pass
    return ""


# ── 规则层冲突检测（导入时自动） ─────────────────────────────────────

def _detect_param_conflicts(doc_id: str, params: list[dict]) -> list[dict]:
    """规则层冲突检测：同名参数数值比对。"""
    if not params:
        return []

    conn = sqlite3.connect(str(DB_PATH))
    conflicts = []

    for p in params:
        pname = p.get("normalized") or p.get("param_name", "")
        pval = p.get("value", "")
        punit = p.get("unit", "")
        if not pname or not pval:
            continue

        rows = conn.execute("""
            SELECT p.value, p.unit, p.doc_id, d.title
            FROM parameters p
            JOIN documents d ON d.id = p.doc_id
            WHERE p.normalized = ? AND p.doc_id != ?
            ORDER BY p.created_at DESC
            LIMIT 10
        """, (pname, doc_id)).fetchall()

        for existing_val, existing_unit, existing_doc_id, existing_title in rows:
            if existing_val == pval:
                continue
            try:
                new_num = float(pval.replace(",", "").replace(" ", ""))
                old_num = float(existing_val.replace(",", "").replace(" ", ""))
                if new_num == old_num:
                    continue
                diff_ratio = abs(new_num - old_num) / max(abs(old_num), abs(new_num), 0.001)
                if diff_ratio < 0.05:
                    continue
            except ValueError:
                pass

            if existing_unit and punit and existing_unit != punit:
                continue

            conflicts.append({
                "param": pname,
                "existing_value": f"{existing_val}{existing_unit}",
                "new_value": f"{pval}{punit}",
                "existing_doc": existing_title,
                "existing_id": existing_doc_id,
            })

    conn.close()
    return conflicts


def _create_param_conflict_yaml(new_doc_title: str, param_name: str,
                                 existing_val: str, new_val: str,
                                 existing_doc: str) -> str:
    """为参数冲突创建 YAML 记录。"""
    CONFLICTS_DIR.mkdir(parents=True, exist_ok=True)
    cid = datetime.now().strftime("CF-PARAM-%Y%m%d-%H%M%S")
    file_path = CONFLICTS_DIR / f"{cid}.yaml"

    data = {
        "title": f"参数冲突: {param_name} — {new_doc_title} vs {existing_doc}",
        "status": "待解决",
        "proposer": "系统（规则层检测）",
        "proposed_at": datetime.now().isoformat(),
        "description": (
            f"参数「{param_name}」在以下文档中存在数值差异：\n"
            f"  {existing_doc}: {existing_val}\n"
            f"  {new_doc_title}: {new_val}\n"
            f"请人工确认哪个值是正确的。"
        ),
        "related_docs": [new_doc_title, existing_doc],
        "sources": [
            {"param": param_name, "value": existing_val, "doc": existing_doc},
            {"param": param_name, "value": new_val, "doc": new_doc_title},
        ],
    }
    import yaml
    with open(file_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
    return cid


# ── Excel 解析主入口 ────────────────────────────────────────────────

def _normalize_param_name(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[（(][^）)]*[）)]", "", name)
    for prefix in ["机器人", "设备", "系统", "仿真", "数据"]:
        if name.startswith(prefix):
            name = name[len(prefix):]
    return name.strip()


def parse_excel(path: Path) -> dict:
    """Excel 结构感知解析，返回结构化数据。"""
    from openpyxl import load_workbook

    wb_ro = load_workbook(str(path), read_only=True, data_only=True)
    sheet_info = {}
    for sheet_name in wb_ro.sheetnames:
        ws = wb_ro[sheet_name]
        sheet_info[sheet_name] = classify_sheet(ws)
    wb_ro.close()

    wb = load_workbook(str(path), data_only=True)

    all_texts = []
    all_params = []
    all_links = []
    structured_summary = {}

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        sheet_type = sheet_info.get(sheet_name, "mixed")

        if sheet_type == "tabular":
            records = extract_tabular(ws, sheet_name)
            for rec in records:
                row_text = " | ".join(f"{k}: {v}" for k, v in rec.items()
                                      if not k.startswith("_"))
                all_texts.append(f"[{sheet_name}] {row_text}")
            structured_summary[sheet_name] = {
                "type": "tabular",
                "rows": len(records),
                "columns": list(records[0].keys()) if records else [],
            }

        elif sheet_type == "spec":
            specs = extract_spec(ws, sheet_name)
            for s in specs:
                line = f"{s['param']}: {s['value']}{s['unit']}"
                if s["note"]:
                    line += f" ({s['note']})"
                all_texts.append(f"[{sheet_name}] {line}")
                normalized = _normalize_param_name(s["param"])
                all_params.append({
                    "param_name": s["param"],
                    "value": s["value"],
                    "unit": s["unit"],
                    "note": s["note"],
                    "normalized": normalized,
                    "sheet_name": sheet_name,
                })
            structured_summary[sheet_name] = {
                "type": "spec",
                "rows": len(specs),
            }

        elif sheet_type == "report":
            regions = extract_report(ws, sheet_name)
            for region in regions:
                lines = [f"{k}: {v}" for k, v in region.items()
                         if not k.startswith("_")]
                all_texts.append(f"[{sheet_name}] " + " | ".join(lines))
            structured_summary[sheet_name] = {
                "type": "report",
                "regions": len(regions),
            }

        else:
            mixed = extract_mixed(ws, sheet_name)
            for para in mixed.get("paragraphs", []):
                all_texts.append(f"[{sheet_name}-文本] {para}")
            for tbl in mixed.get("tables", []):
                row_texts = [
                    " | ".join(c for c in r if c.strip())
                    for r in tbl if any(c.strip() for c in r)
                ]
                all_texts.extend(f"[{sheet_name}-表格] {t}" for t in row_texts)
            structured_summary[sheet_name] = {
                "type": "mixed",
                "paragraphs": len(mixed.get("paragraphs", [])),
                "tables": len(mixed.get("tables", [])),
            }

        links = detect_hyperlinks(ws, sheet_name)
        all_links.extend(links)

    wb.close()

    return {
        "text": "\n".join(all_texts),
        "params": all_params,
        "links": all_links,
        "structured_summary": structured_summary,
    }


# ── extract_text 增强 ───────────────────────────────────────────────

def extract_text(path: Path, fetch_links: bool = True) -> dict:
    """提取文档全文 + 结构化数据。"""
    ext = path.suffix.lower()
    result = {"text": "", "params": [], "links": [], "summary": {}}

    if ext == ".md":
        result["text"] = path.read_text(encoding="utf-8")
    elif ext == ".txt":
        result["text"] = path.read_text(encoding="utf-8")
    elif ext == ".docx":
        try:
            from docx import Document
            doc = Document(str(path))
            paras = [p.text for p in doc.paragraphs]
            result["text"] = "\n".join(paras)
            tables = []
            for table in doc.tables:
                rows = []
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    rows.append(cells)
                tables.append(rows)
                result["text"] += "\n\n[表格]\n" + "\n".join(
                    " | ".join(cells) for cells in rows
                )
            result["summary"]["tables"] = len(tables)
        except ImportError:
            result["text"] = f"[需要 python-docx]\n{path}"
    elif ext == ".xlsx":
        parsed = parse_excel(path)
        result["text"] = parsed["text"]
        result["params"] = parsed["params"]
        result["links"] = parsed["links"]
        result["summary"] = parsed["structured_summary"]
    elif ext == ".pptx":
        try:
            from pptx import Presentation
            texts = []
            for slide in Presentation(str(path)).slides:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        texts.append(shape.text)
            result["text"] = "\n".join(texts)
        except ImportError:
            result["text"] = f"[需要 python-pptx]\n{path}"
    else:
        result["text"] = ""

    # 超链接递归下载
    if fetch_links and result["links"]:
        total_links = len(result["links"])
        print(f"\n  🔗 发现 {total_links} 个超链接，开始递归下载...")
        fetched_count = 0
        for i, link in enumerate(result["links"]):
            if i >= 10:
                print(f"    已达到处理上限(10)，剩余 {total_links - 10} 跳过")
                break
            content = fetch_link_target(link)
            result["links"][i]["target_text"] = content
            if content and not content.startswith("["):
                result["links"][i]["fetched"] = 1
                fetched_count += 1
                result["text"] += f"\n\n[超链接: {link['url']}]\n{content[:2000]}"
            status = "✅" if result["links"][i].get("fetched") else "⏭️"
            print(f"    {status} [{link['link_type']}] {link['url'][:60]}...")

        print(f"  📊 校验: {fetched_count}/{total_links} 链接已处理")

    return result


# ── 参数元数据持久化 ────────────────────────────────────────────────

def _store_parameters(conn, doc_id: str, params: list[dict]):
    for i, p in enumerate(params):
        param_id = hashlib.md5(f"{doc_id}_{p['param_name']}_{i}".encode()).hexdigest()[:16]
        conn.execute("""
            INSERT OR REPLACE INTO parameters
                (id, doc_id, param_name, value, unit, sheet_name, row_idx, normalized, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            param_id, doc_id,
            p["param_name"], p.get("value", ""), p.get("unit", ""),
            p.get("sheet_name", ""), i,
            p.get("normalized", _normalize_param_name(p["param_name"])),
            "excel",
        ))


def _store_hyperlinks(conn, doc_id: str, links: list[dict]):
    for i, link in enumerate(links):
        lid = hashlib.md5(f"{doc_id}_link_{i}".encode()).hexdigest()[:16]
        conn.execute("""
            INSERT OR REPLACE INTO hyperlinks
                (id, doc_id, sheet_name, row_idx, col_idx, cell_text, url,
                 link_type, target_text, fetched, fetch_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            lid, doc_id,
            link.get("_sheet", ""), link.get("row", 0), link.get("col", 0),
            link.get("cell_text", ""), link.get("url", ""),
            link.get("link_type", "unknown"),
            link.get("target_text", ""),
            1 if link.get("fetched") else 0,
            "",
        ))


# ── 标题提取 ───────────────────────────────────────────────────────

def extract_title(content: str, filename: str) -> str:
    m = re.search(r"^#\s+(.+)", content, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return filename.rsplit(".", 1)[0]


# ── 语义冲突自动创建（保持原接口） ─────────────────────────────────

def _auto_create_conflict(new_title: str, conflict: dict):
    target = conflict["doc"]
    cid = datetime.now().strftime("CF-AUTO-%Y%m%d-%H%M%S")
    file_path = CONFLICTS_DIR / f"{cid}.yaml"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    kw = ", ".join(conflict.get("overlap_keywords", [])[:5])
    data = {
        "title": f"疑似重复/冲突: {new_title} ↔ {target['title']}",
        "status": "待解决",
        "proposer": "系统（语义检测）",
        "proposed_at": datetime.now().isoformat(),
        "description": (
            f"导入文档「{new_title}」与现有文档「{target['title']}」"
            f"语义相似度 {conflict['score']:.0%}。\n"
            f"重叠关键词: {kw}\n"
            f"请人工确认是否为重复定义或存在规范冲突。"
        ),
        "related_docs": [new_title, target["title"]],
        "sources": [{"doc": target["title"], "score": conflict["score"]}],
    }
    import yaml
    with open(file_path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
    return cid


# ── 导入单文件 ─────────────────────────────────────────────────────

def import_file(path: Path, category: str = "",
                fetch_links: bool = True,
                skip_semantic: bool = False) -> dict:
    """导入单个文件。

    V2 增强:
      - Excel 解析返回结构化数据 (params, links, summary)
      - 参数自动写入 parameters 表
      - 超链接写入 hyperlinks 表 + 递归下载目标内容
      - 导入时规则层参数冲突检测
    """
    parsed = extract_text(path, fetch_links=fetch_links)
    text = parsed["text"]
    if not text or len(text) < 20:
        return {"skipped": True, "reason": "内容过短"}

    title = extract_title(text, path.name)
    doc_id = hashlib.md5(str(path.resolve()).encode()).hexdigest()[:16]

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        INSERT OR REPLACE INTO documents
            (id, title, content, category, filename)
        VALUES (?, ?, ?, ?, ?)
    """, (doc_id, title, text, category, path.name))

    if parsed["params"]:
        _store_parameters(conn, doc_id, parsed["params"])
    if parsed["links"]:
        _store_hyperlinks(conn, doc_id, parsed["links"])

    conn.commit()
    conn.close()

    # 规则层冲突检测
    if parsed["params"]:
        param_conflicts = _detect_param_conflicts(doc_id, parsed["params"])
        for pc in param_conflicts:
            cid = _create_param_conflict_yaml(
                title, pc["param"],
                pc["existing_value"], pc["new_value"],
                pc["existing_doc"],
            )
            print(f"  🔴 参数冲突: {pc['param']}: "
                  f"{pc['existing_value']} vs {pc['new_value']} → {cid}")

    # 语义层冲突检测（原逻辑）
    if not skip_semantic:
        try:
            sys.path.insert(0, str(BASE / "backend"))
            import search_engine
            conflicts = search_engine.detect_conflicts(title, text)
            for c in conflicts:
                cid = _auto_create_conflict(title, c)
                print(f"  ⚠️  语义冲突 → {cid}")
        except Exception:
            pass

    return {
        "id": doc_id,
        "title": title,
        "skipped": False,
        "params_count": len(parsed["params"]),
        "links_count": len(parsed["links"]),
        "summary": parsed.get("summary", {}),
    }


# ── 导入目录 ───────────────────────────────────────────────────────

def import_dir(dir_path: Path, category: str = "",
               fetch_links: bool = True,
               skip_semantic: bool = False):
    """递归导入目录。"""
    files = sorted(dir_path.rglob("*"))
    files = [f for f in files if f.suffix.lower() in SUPPORTED]
    total = len(files)
    imported = 0

    print(f"\n📂 扫描到 {total} 个文件")
    for i, f in enumerate(files, 1):
        rel = f.relative_to(dir_path)
        subcat = str(rel.parent) if str(rel.parent) != "." else category
        result = import_file(f, category=subcat,
                             fetch_links=fetch_links,
                             skip_semantic=skip_semantic)
        if not result["skipped"]:
            imported += 1
            details = []
            pc = result.get("params_count", 0)
            lc = result.get("links_count", 0)
            if pc:
                details.append(f"{pc}参数")
            if lc:
                details.append(f"{lc}链接")
            extra = " (" + ", ".join(details) + ")" if details else ""
            print(f"  [OK] {f.name}{extra}")
        else:
            print(f"  [SKIP] {f.name} ({result.get('reason', '')})")

    print(f"\n📊 导入完成: {imported}/{total}")


# ── 主入口 ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="文档导入 V2 — Excel 结构感知 + 超链接递归")
    parser.add_argument("path", help="文件或目录路径")
    parser.add_argument("--category", default="", help="分类标签")
    parser.add_argument("--no-fetch-links", action="store_true",
                        help="跳过超链接递归下载")
    parser.add_argument("--no-semantic", action="store_true",
                        help="跳过语义冲突检测（仅规则层）")
    args = parser.parse_args()

    _init_structured_tables()

    p = Path(args.path)
    if not p.exists():
        print(f"路径不存在: {p}")
        sys.exit(1)

    if p.is_file():
        result = import_file(p, category=args.category,
                             fetch_links=not args.no_fetch_links,
                             skip_semantic=args.no_semantic)
        status = "OK" if not result.get("skipped") else result.get("reason", "SKIP")
        params_info = f", {result['params_count']}参数" if result.get("params_count") else ""
        links_info = f", {result['links_count']}链接" if result.get("links_count") else ""
        print(f"\n  导入: {result.get('title', p.name)} ({status}{params_info}{links_info})")
    else:
        import_dir(p, category=args.category,
                   fetch_links=not args.no_fetch_links,
                   skip_semantic=args.no_semantic)

    if not args.no_semantic:
        print("\n重建语义索引...")
        sys.path.insert(0, str(BASE / "backend"))
        from backend.server import rebuild_search_index
        count = rebuild_search_index()
        print(f"语义索引重建完成: {count} 篇")
