"""
生成三种典型形态的样例工资表，用于端到端验证与交付演示
    1) sample_long.xlsx   长表：一行 = 员工 × 月（含月份列、含客户既有结果列）
    2) sample_wide.xlsx   宽表：一行 = 员工，月份横向铺开
    3) sample_single.csv  单月表：一行 = 员工，仅一个月

注意：长表中的「个人所得税」列刻意使用「按月单独计税」的错误算法生成，
      用于演示闪电校验如何发现客户侧与累计预扣法之间的税额差异。
"""
from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook

OUT = Path(__file__).resolve().parent

BRACKETS = [
    (36000, 0.03, 0), (144000, 0.10, 2520), (300000, 0.20, 16920),
    (420000, 0.25, 31920), (660000, 0.30, 52920), (960000, 0.35, 85920),
    (float("inf"), 0.45, 181920),
]
BRACKETS_MONTH = [
    (3000, 0.03, 0), (12000, 0.10, 210), (25000, 0.20, 1410),
    (35000, 0.25, 2660), (55000, 0.30, 4410), (80000, 0.35, 7160),
    (float("inf"), 0.45, 15160),
]

# 上海 2026：社保 7460~37302（1-6月）/ 7546~37731（7-12月），公积金 2690~37302 / 2740~37731
SOCIAL = {1: (7460, 37302), 7: (7546, 37731)}
HOUSING = {1: (2690, 37302), 7: (2740, 37731)}


def bracket_tax(x: float, table) -> float:
    if x <= 0:
        return 0.0
    for upper, rate, quick in table:
        if x <= upper:
            return round(x * rate - quick, 2)
    return 0.0


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(v, hi))


def social_of(gross: float, month: int) -> dict[str, float]:
    lo, hi = SOCIAL[1 if month <= 6 else 7]
    hlo, hhi = HOUSING[1 if month <= 6 else 7]
    sb = clamp(gross, lo, hi)
    hb = clamp(gross, hlo, hhi)
    return {
        "social_base": round(sb, 2),
        "housing_base": round(hb, 2),
        "pension": round(sb * 0.08, 2),
        "medical": round(sb * 0.02, 2),
        "unemployment": round(sb * 0.005, 2),
        "housing": round(hb * 0.07),
    }


EMPLOYEES = [
    {"id": "E001", "name": "张伟", "idno": "310101199001011234", "dept": "研发部",
     "basic": 15000, "post": 3000, "perf": 2000, "allow": 500, "bonus": 60000,
     "ded": {"children": 2000, "rent": 1500}, "months": range(1, 13)},
    {"id": "E002", "name": "李娜", "idno": "310101199203054321", "dept": "市场部",
     "basic": 8000, "post": 1000, "perf": 1000, "allow": 300, "bonus": 0,
     "ded": {"elderly": 3000}, "months": range(1, 13)},
    {"id": "E003", "name": "王强", "idno": "310101198507156789", "dept": "研发部",
     "basic": 25000, "post": 5000, "perf": 5000, "allow": 1000, "bonus": 120000,
     "ded": {"children": 2000, "mortgage": 1000}, "months": range(1, 13)},
    {"id": "E004", "name": "赵敏", "idno": "310101200003120987", "dept": "行政部",
     "basic": 6000, "post": 500, "perf": 500, "allow": 200, "bonus": 0,
     "ded": {}, "months": range(1, 13)},
    {"id": "E005", "name": "陈杰", "idno": "310101199811204455", "dept": "销售部",
     "basic": 20000, "post": 0, "perf": 3000, "allow": 500, "bonus": 0,
     "ded": {"rent": 1500}, "months": range(7, 13)},   # 7 月入职
]


def build_rows() -> list[dict]:
    rows: list[dict] = []
    for e in EMPLOYEES:
        for month in e["months"]:
            gross = e["basic"] + e["post"] + e["perf"] + e["allow"]
            si = social_of(gross, month)
            contrib = round(si["pension"] + si["medical"] + si["unemployment"] + si["housing"], 2)
            special = sum(e["ded"].values())
            # —— 客户侧常见的错误算法：按月单独计税，而非累计预扣法
            taxable_wrong = gross - 5000 - contrib - special
            tax_wrong = bracket_tax(taxable_wrong, BRACKETS_MONTH)
            rows.append({
                "工号": e["id"], "姓名": e["name"], "身份证号": e["idno"], "部门": e["dept"],
                "参保城市": "上海", "年度": 2026, "月份": month,
                "基本工资": e["basic"], "岗位工资": e["post"], "绩效工资": e["perf"], "津贴补贴": e["allow"],
                "应发工资": gross,
                "社保基数": si["social_base"], "公积金基数": si["housing_base"],
                "养老": si["pension"], "医疗": si["medical"], "失业": si["unemployment"],
                "公积金": si["housing"],
                "子女教育": e["ded"].get("children", 0), "住房租金": e["ded"].get("rent", 0),
                "赡养老人": e["ded"].get("elderly", 0), "住房贷款利息": e["ded"].get("mortgage", 0),
                "全年一次性奖金": e["bonus"] if month == 12 else 0,
                "个人所得税": tax_wrong,
                "实发工资": round(gross - contrib - tax_wrong, 2),
            })
    return rows


def write_long(rows: list[dict]) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "2026年工资表"
    # 顶部加两行说明，验证表头自动探测能力
    ws.append(["某某科技有限公司", "", "", "2026 年度工资薪金明细表"])
    ws.append(["制表人：客户方 HR", "", "", "单位：元"])
    headers = list(rows[0].keys())
    ws.append(headers)
    for r in rows:
        ws.append([r[h] for h in headers])
    path = OUT / "sample_long.xlsx"
    wb.save(path)
    return path


def write_wide(rows: list[dict]) -> Path:
    """宽表：一行一名员工，每月铺 基本工资 / 应发工资 两列"""
    wb = Workbook()
    ws = wb.active
    ws.title = "全年工资汇总"
    by_emp: dict[str, list[dict]] = {}
    for r in rows:
        by_emp.setdefault(r["工号"], []).append(r)

    headers = ["工号", "姓名", "部门", "参保城市", "年度"]
    for m in range(1, 13):
        headers += [f"{m}月基本工资", f"{m}月应发工资"]
    ws.append(headers)
    for eid, items in by_emp.items():
        first = items[0]
        line = [eid, first["姓名"], first["部门"], first["参保城市"], 2026]
        for m in range(1, 13):
            hit = next((i for i in items if i["月份"] == m), None)
            line += [hit["基本工资"] if hit else None, hit["应发工资"] if hit else None]
        ws.append(line)
    path = OUT / "sample_wide.xlsx"
    wb.save(path)
    return path


def write_single(rows: list[dict]) -> Path:
    items = [r for r in rows if r["月份"] == 3]
    keep = ["工号", "姓名", "身份证号", "部门", "参保城市", "年度",
            "基本工资", "岗位工资", "绩效工资", "津贴补贴", "应发工资",
            "社保基数", "公积金基数", "子女教育", "住房租金", "赡养老人", "住房贷款利息"]
    path = OUT / "sample_single.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keep)
        w.writeheader()
        for r in items:
            w.writerow({k: r[k] for k in keep})
    return path


if __name__ == "__main__":
    rows = build_rows()
    print("长表：", write_long(rows))
    print("宽表：", write_wide(rows))
    print("单月表：", write_single(rows))
    print(f"共 {len(rows)} 条记录")
