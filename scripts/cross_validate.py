#!/usr/bin/env python3
"""
参数交叉验证 — 确定性比对

从知识图谱中提取所有带数值的声明(claim)，
按参数名分组，检测同一参数在不同文档中的取值差异。

这是确定性比对，不依赖 LLM 判断。
"""

import json
import re
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).resolve().parent.parent
GRAPH_PATH = BASE / "wiki" / ".understand-anything" / "knowledge-graph.json"
OUTPUT_PATH = BASE / "wiki" / ".understand-anything" / "cross-validation.json"


def extract_numerical_values(content: str) -> list[dict]:
    """从声明内容中提取数值和单位。"""
    results = []

    # 匹配模式：数值 + 单位
    patterns = [
        # 精度/偏差：±0.05mm, ≤5mm, ≥20m
        (r'[±≤≥<>]\s*(\d+\.?\d*)\s*(mm|m|µm|µm/m|°|%|s|kg|ton|N·m|N|m/s²)', 'precision'),
        # 范围：5-10mm, 10~20%
        (r'(\d+\.?\d*)\s*[-~]\s*(\d+\.?\d*)\s*(mm|m|µm|°|%|s|kg)', 'range'),
        # 具体值：6米, 4吨, 100%
        (r'(\d+\.?\d*)\s*(mm|m|µm|µm/m|°|%|s|kg|ton|N·m|N|m/s²|个|条|套)', 'value'),
        # 版本号：V1.1, 16.1.2
        (r'[Vv]?(\d+\.\d+(?:\.\d+)?)', 'version'),
    ]

    for pattern, ptype in patterns:
        matches = re.finditer(pattern, content)
        for m in matches:
            if ptype == 'range':
                results.append({
                    'type': ptype,
                    'min': float(m.group(1)),
                    'max': float(m.group(2)),
                    'unit': m.group(3),
                    'raw': m.group(0),
                })
            elif ptype == 'version':
                results.append({
                    'type': ptype,
                    'value': m.group(1),
                    'raw': m.group(0),
                })
            else:
                results.append({
                    'type': ptype,
                    'value': float(m.group(1)),
                    'unit': m.group(2) if len(m.groups()) > 1 else '',
                    'raw': m.group(0),
                })

    return results


def normalize_param_name(name: str) -> str:
    """标准化参数名，用于分组比较。"""
    name = name.lower().strip()
    # 移除常见前缀/后缀
    for prefix in ['机器人', '设备', '仿真', '数据', '模型', '系统']:
        if name.startswith(prefix):
            name = name[len(prefix):]
    # 移除括号内容
    name = re.sub(r'[（(].*?[）)]', '', name)
    return name.strip()


def extract_equipment_type(title: str, content: str) -> str:
    """提取设备类型，用于区分同一参数在不同设备上的值。"""
    # 常见设备类型
    equipment_types = [
        '机器人', '七轴', '管线包', '换枪盘', '视觉系统', '涂胶视觉',
        '夹具', '抓手', '焊枪', '焊钳', '修磨器', '铆枪', '焊机',
        '输送机', '升降机', '滚床', '转台', '移栽机',
        '控制柜', '排烟除尘设备', '光栅', '扫描仪',
    ]

    # 从标题中提取
    for eq_type in equipment_types:
        if eq_type in title:
            return eq_type

    # 从内容中提取（第一行通常是设备名）
    first_line = content.split('\n')[0] if content else ''
    for eq_type in equipment_types:
        if eq_type in first_line:
            return eq_type

    return '通用'


def main():
    with open(GRAPH_PATH, "r", encoding="utf-8") as f:
        graph = json.load(f)

    # 提取所有 claim 节点
    claims = [n for n in graph["nodes"] if n["type"] == "claim"]
    print(f"📊 共 {len(claims)} 条声明待分析")

    # 按参数名+设备类型分组
    param_groups = defaultdict(list)

    for claim in claims:
        title = claim.get("name", "")
        content = claim.get("summary", "")
        source_doc = claim.get("claimMeta", {}).get("sourceDoc", "")

        # 提取数值
        values = extract_numerical_values(content)
        if not values:
            continue

        # 标准化参数名
        norm_name = normalize_param_name(title)

        # 提取设备类型
        equipment_type = extract_equipment_type(title, content)

        # 分组键：参数名 + 设备类型
        group_key = f"{norm_name}|{equipment_type}"

        param_groups[group_key].append({
            "title": title,
            "content": content,
            "source_doc": source_doc,
            "equipment_type": equipment_type,
            "values": values,
        })

    # 检测冲突
    conflicts = []

    for param_name, entries in param_groups.items():
        if len(entries) < 2:
            continue

        # 比较每对文档
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                doc1 = entries[i]
                doc2 = entries[j]

                # 比较数值
                for v1 in doc1["values"]:
                    for v2 in doc2["values"]:
                        conflict = compare_values(v1, v2, param_name, doc1, doc2)
                        if conflict:
                            conflicts.append(conflict)

    # 去重
    unique_conflicts = deduplicate_conflicts(conflicts)

    # 输出
    output = {
        "total_claims": len(claims),
        "claims_with_values": sum(1 for c in claims if extract_numerical_values(c.get("summary", ""))),
        "param_groups": len(param_groups),
        "conflicts_found": len(unique_conflicts),
        "conflicts": unique_conflicts,
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*60}")
    print(f"📊 交叉验证完成!")
    print(f"  有数值的声明: {output['claims_with_values']}")
    print(f"  参数分组: {output['param_groups']}")
    print(f"  发现冲突: {output['conflicts_found']}")
    print(f"📁 结果: {OUTPUT_PATH}")

    if unique_conflicts:
        print(f"\n🔴 发现的参数冲突:")
        for i, c in enumerate(unique_conflicts, 1):
            print(f"\n  {i}. {c['param']} ({c.get('equipment_type', '通用')})")
            print(f"     文档1: {c['doc1']} → {c['value1']}")
            print(f"     文档2: {c['doc2']} → {c['value2']}")
            print(f"     类型: {c['conflict_type']}")
            print(f"     详情: {c['detail']}")


def compare_values(v1: dict, v2: dict, param_name: str, doc1: dict, doc2: dict) -> dict | None:
    """比较两个数值，判断是否冲突。"""

    # 版本号比较
    if v1["type"] == "version" and v2["type"] == "version":
        if v1["value"] != v2["value"]:
            return {
                "param": param_name,
                "equipment_type": doc1.get("equipment_type", "通用"),
                "doc1": doc1["source_doc"],
                "doc2": doc2["source_doc"],
                "value1": v1["raw"],
                "value2": v2["raw"],
                "conflict_type": "version_mismatch",
                "detail": f"版本不一致: {v1['value']} vs {v2['value']}",
            }
        return None

    # 精度/偏差比较
    if v1["type"] == "precision" and v2["type"] == "precision":
        if v1["unit"] != v2["unit"]:
            return None  # 单位不同，不比较

        # 检查是否是同一量级
        if abs(v1["value"] - v2["value"]) / max(v1["value"], v2["value"]) > 0.1:
            return {
                "param": param_name,
                "equipment_type": doc1.get("equipment_type", "通用"),
                "doc1": doc1["source_doc"],
                "doc2": doc2["source_doc"],
                "value1": v1["raw"],
                "value2": v2["raw"],
                "conflict_type": "value_mismatch",
                "detail": f"数值差异: {v1['value']}{v1['unit']} vs {v2['value']}{v2['unit']}",
            }
        return None

    # 具体值比较
    if v1["type"] == "value" and v2["type"] == "value":
        if v1["unit"] != v2["unit"]:
            return None  # 单位不同，不比较

        # 检查是否是同一量级
        if abs(v1["value"] - v2["value"]) / max(v1["value"], v2["value"]) > 0.1:
            return {
                "param": param_name,
                "equipment_type": doc1.get("equipment_type", "通用"),
                "doc1": doc1["source_doc"],
                "doc2": doc2["source_doc"],
                "value1": v1["raw"],
                "value2": v2["raw"],
                "conflict_type": "value_mismatch",
                "detail": f"数值差异: {v1['value']}{v1['unit']} vs {v2['value']}{v2['unit']}",
            }
        return None

    # 范围比较
    if v1["type"] == "range" and v2["type"] == "range":
        if v1["unit"] != v2["unit"]:
            return None

        # 检查范围是否重叠
        if v1["max"] < v2["min"] or v2["max"] < v1["min"]:
            return {
                "param": param_name,
                "equipment_type": doc1.get("equipment_type", "通用"),
                "doc1": doc1["source_doc"],
                "doc2": doc2["source_doc"],
                "value1": v1["raw"],
                "value2": v2["raw"],
                "conflict_type": "range_no_overlap",
                "detail": f"范围不重叠: {v1['raw']} vs {v2['raw']}",
            }
        return None

    return None


def deduplicate_conflicts(conflicts: list[dict]) -> list[dict]:
    """去重冲突。"""
    seen = set()
    unique = []

    for c in conflicts:
        key = (
            c["param"],
            tuple(sorted([c["doc1"], c["doc2"]])),
            c["value1"],
            c["value2"],
        )
        if key not in seen:
            seen.add(key)
            unique.append(c)

    return unique


if __name__ == "__main__":
    main()
