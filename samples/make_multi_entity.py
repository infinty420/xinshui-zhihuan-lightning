"""
多主体差异化模板验证
====================
模拟三菱化学集团 18 家经营主体报表模板不统一的真实场景：
每家用不同表头写法、不同表形态、不同城市，验证适配层能否零改代码硬吃下来。

生成 6 个代表性主体的工资表到 samples/multi_entity/ 下。
"""
from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook

OUT = Path(__file__).resolve().parent / "multi_entity"
OUT.mkdir(exist_ok=True)

# 每家主体的「表头方言」与城市、表形态
ENTITIES = [
    {
        "file": "01_三菱化学_上海_长表.xlsx", "city": "上海", "form": "long",
        "headers": ["工号", "姓名", "身份证号码", "所属部门", "参保城市", "年度", "月份",
                    "基本工资", "岗位工资", "绩效奖金", "各类补贴", "应发合计",
                    "社保缴费基数", "住房公积金基数", "子女教育支出", "住房租金", "赡养老人"],
        "map": {"工号": "empno", "姓名": "nm", "身份证号码": "idcard", "所属部门": "dept",
                "参保城市": "city", "年度": "yr", "月份": "mo", "基本工资": "base",
                "岗位工资": "post", "绩效奖金": "perf", "各类补贴": "allow", "应发合计": "gross",
                "社保缴费基数": "sbase", "住房公积金基数": "hbase",
                "子女教育支出": "children", "住房租金": "rent", "赡养老人": "elderly"},
    },
    {
        "file": "02_三菱化学_北京_宽表.xlsx", "city": "北京", "form": "wide",
        "headers": ["员工编号", "员工姓名", "部门名称", "社保城市", "年份",
                    "1月应发工资", "1月基础工资", "2月应发工资", "2月基础工资",
                    "3月应发工资", "3月基础工资"],
        "map": {},
    },
    {
        "file": "03_三菱化学_广州_单月.csv", "city": "广州", "form": "single",
        "headers": ["职工号", "职工姓名", "身份证号", "成本中心", "参保地", "年度",
                    "基础薪", "岗位薪", "浮动工资", "津贴", "应发工资",
                    "养老", "医疗", "失业", "公积金", "住房租金", "继续教育"],
        "map": {},
    },
]

PEOPLE = [
    {"empno": "MC1001", "nm": "陈志远", "idcard": "310101199001011234", "dept": "生产部",
     "base": 16000, "post": 2500, "perf": 1800, "allow": 600,
     "ded": {"children": 2000, "rent": 1500, "elderly": 0, "education": 0}},
    {"empno": "MC1002", "nm": "林静怡", "idcard": "110101199205054321", "dept": "品保部",
     "base": 11000, "post": 1500, "perf": 1200, "allow": 400,
     "ded": {"children": 0, "rent": 1500, "elderly": 3000, "education": 400}},
    {"empno": "MC1003", "nm": "黄浩然", "idcard": "440101198711156789", "dept": "销售部",
     "base": 22000, "post": 3000, "perf": 4500, "allow": 900,
     "ded": {"children": 2000, "rent": 0, "elderly": 1500, "education": 0}},
    {"empno": "MC1004", "nm": "吴思婷", "idcard": "440101199503120987", "dept": "人事部",
     "base": 9000, "post": 800, "perf": 900, "allow": 300,
     "ded": {"children": 0, "rent": 1500, "elderly": 0, "education": 0}},
]


def write_long(spec: dict) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "工资明细"
    ws.append(["三菱化学（中国）有限公司", "", "", "", "2026 年度工资明细表"])
    ws.append(["单位：元", "", "", "", "制表：薪酬共享中心"])
    ws.append(spec["headers"])
    for p in PEOPLE:
        for m in range(1, 13):
            gross = p["base"] + p["post"] + p["perf"] + p["allow"]
            row = {
                "工号": p["empno"], "姓名": p["nm"], "身份证号码": p["idcard"],
                "所属部门": p["dept"], "参保城市": spec["city"], "年度": 2026, "月份": m,
                "基本工资": p["base"], "岗位工资": p["post"], "绩效奖金": p["perf"],
                "各类补贴": p["allow"], "应发合计": gross,
                "社保缴费基数": gross, "住房公积金基数": gross,
                "子女教育支出": p["ded"]["children"], "住房租金": p["ded"]["rent"],
                "赡养老人": p["ded"]["elderly"],
            }
            ws.append([row[h] for h in spec["headers"]])
    path = OUT / spec["file"]
    wb.save(path)
    return path


def write_wide(spec: dict) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "季度汇总"
    ws.append(spec["headers"])
    for p in PEOPLE:
        gross = p["base"] + p["post"] + p["perf"] + p["allow"]
        line = [p["empno"], p["nm"], p["dept"], spec["city"], 2026]
        for _ in range(1, 4):
            line += [gross, p["base"]]
        ws.append(line)
    path = OUT / spec["file"]
    wb.save(path)
    return path


def write_single(spec: dict) -> Path:
    # 广州主体上半月数据，故意把「养老/医疗/失业/公积金」做成已缴金额列
    path = OUT / spec["file"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=spec["headers"])
        w.writeheader()
        for p in PEOPLE:
            gross = p["base"] + p["post"] + p["perf"] + p["allow"]
            # 广州 2026 上半年：社保 5510~27549，公积金 2500~39828
            sbase = max(5510, min(gross, 27549))
            hbase = max(2500, min(gross, 39828))
            w.writerow({
                "职工号": p["empno"], "职工姓名": p["nm"], "身份证号": p["idcard"],
                "成本中心": p["dept"], "参保地": spec["city"], "年度": 2026,
                "基础薪": p["base"], "岗位薪": p["post"], "浮动工资": p["perf"], "津贴": p["allow"],
                "应发工资": gross,
                "养老": round(sbase * 0.08, 2), "医疗": round(sbase * 0.02, 2),
                "失业": round(sbase * 0.002, 2), "公积金": round(hbase * 0.05),
                "住房租金": p["ded"]["rent"], "继续教育": p["ded"]["education"],
            })
    return path


if __name__ == "__main__":
    a = write_long(ENTITIES[0])
    b = write_wide(ENTITIES[1])
    c = write_single(ENTITIES[2])
    print("长表：", a.name)
    print("宽表：", b.name)
    print("单月：", c.name)
