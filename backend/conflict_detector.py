"""冲突检测器 — 冲突清单 → 解决方案 → 最终规范

数据存储在 data/conflicts/ 下的 YAML 文件。
每个冲突一个文件，包含：
  - 标题、描述、提出人、提出时间
  - 关联文档
  - 解决方案列表（谁、方案、时间）
  - 最终状态（待解决 / 已解决 / 已关闭）
"""

import os
import yaml
from datetime import datetime
from pathlib import Path

CONFLICTS_DIR = Path(__file__).resolve().parent.parent / "data" / "conflicts"


def _ensure_dir():
    CONFLICTS_DIR.mkdir(parents=True, exist_ok=True)


def _conflict_path(cid: str) -> Path:
    return CONFLICTS_DIR / f"{cid}.yaml"


def list_conflicts(status: str = "") -> list[dict]:
    """列出冲突，按时间倒序。"""
    _ensure_dir()
    files = sorted(CONFLICTS_DIR.glob("*.yaml"), reverse=True)
    result = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            c = yaml.safe_load(fh) or {}
        if status and c.get("status") != status:
            continue
        c["id"] = f.stem
        result.append(c)
    return result


def get_conflict(cid: str) -> dict:
    """获取单个冲突详情。"""
    p = _conflict_path(cid)
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        c = yaml.safe_load(f) or {}
    c["id"] = cid
    return c


def create_conflict(data: dict) -> str:
    """创建冲突记录。返回冲突 ID。"""
    _ensure_dir()
    cid = datetime.now().strftime("CF-%Y%m%d-%H%M%S")
    data.setdefault("status", "待解决")
    data.setdefault("proposed_at", datetime.now().isoformat())
    data.setdefault("solutions", [])
    data.setdefault("related_docs", [])
    with open(_conflict_path(cid), "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
    return cid


def add_solution(cid: str, solution: dict) -> bool:
    """为冲突添加解决方案。"""
    p = _conflict_path(cid)
    if not p.exists():
        return False
    with open(p, encoding="utf-8") as f:
        c = yaml.safe_load(f) or {}
    c.setdefault("solutions", [])
    solution.setdefault("proposed_at", datetime.now().isoformat())
    c["solutions"].append(solution)
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(c, f, allow_unicode=True, default_flow_style=False)
    return True


def resolve_conflict(cid: str, final_spec: str = "") -> bool:
    """标记冲突已解决，记录最终规范。"""
    p = _conflict_path(cid)
    if not p.exists():
        return False
    with open(p, encoding="utf-8") as f:
        c = yaml.safe_load(f) or {}
    c["status"] = "已解决"
    c["resolved_at"] = datetime.now().isoformat()
    if final_spec:
        c["final_spec"] = final_spec
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(c, f, allow_unicode=True, default_flow_style=False)
    return True


def get_stats() -> dict:
    """冲突统计。"""
    all_c = list_conflicts()
    pending = [c for c in all_c if c.get("status") == "待解决"]
    resolved = [c for c in all_c if c.get("status") == "已解决"]
    return {
        "total": len(all_c),
        "pending": len(pending),
        "resolved": len(resolved),
    }
