"""
核心算法单元测试 —— 全部用「手算值」比对，确保引擎口径正确
运行：python tests/test_core.py
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.engine import Employee, MonthInput, compute_employee, run_payroll
from core.policy import D, Policy, money
from core.validator import Validator

PASS, FAIL = 0, 0


def check(name: str, actual, expected, tol=Decimal("0.01")) -> None:
    global PASS, FAIL
    a, e = Decimal(str(actual)), Decimal(str(expected))
    ok = abs(a - e) <= tol
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: 实际 {a}  期望 {e}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def check_str(name: str, actual, expected) -> None:
    global PASS, FAIL
    ok = str(actual) == str(expected)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: 实际 {actual}  期望 {expected}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def main() -> int:
    policy = Policy.load()

    print("\n== 1. 综合所得税率表 ==")
    check("36000 元（3%档）", policy.tax_of(D(36000))[0], 1080)
    check("36000.01 元（10%档）", policy.tax_of(D("36000.01"))[0], Decimal("1080.001"))
    check("144000 元（10%档顶）", policy.tax_of(D(144000))[0], 11880)
    check("300000 元（20%档顶）", policy.tax_of(D(300000))[0], 43080)
    check("960000 元（35%档顶）", policy.tax_of(D(960000))[0], 250080)
    check("1000000 元（45%档）", policy.tax_of(D(1000000))[0], 268080)
    check("0 元不纳税", policy.tax_of(D(0))[0], 0)

    print("\n== 2. 全年一次性奖金单独计税（按月换算表）==")
    check("36000 元（36000/12=3000 → 3%）", policy.annual_bonus_tax(D(36000))[0], 1080)
    check("60000 元（5000 → 10%-210）", policy.annual_bonus_tax(D(60000))[0], 5790)
    check("120000 元（10000 → 10%-210）", policy.annual_bonus_tax(D(120000))[0], 11790)
    check("300000 元（25000 → 20%-1410）", policy.annual_bonus_tax(D(300000))[0], 58590)

    print("\n== 3. 月度累计预扣法（上海 2026 / 月薪 20500 / 五险一金 3587.5 / 专项附加 3500）==")
    emp = Employee(
        employee_id="T001", name="测试", city_code="shanghai", city_name="上海", year=2026,
        annual_bonus_method="separate",
    )
    months = [
        MonthInput(
            month=m, gross=D(20500),
            contributions_override={
                "pension": D(1640), "medical": D(410), "unemployment": D("102.5"), "housing": D(1435),
            },
            deductions={"children": D(2000), "rent": D(1500)},
        )
        for m in range(1, 13)
    ]
    res = compute_employee(emp, months, policy, policy.cities["shanghai"])
    m1 = res.months[0]
    # 1 月：20500 - 5000 - 3587.5 - 3500 = 8412.5 → ×3% = 252.375
    check("1月应纳税所得额", m1.cum_taxable, "8412.50")
    check("1月预扣个税", m1.tax, "252.38")
    check("1月实发", m1.net, "16660.12")
    # 5 月：42062.5 → 10%-2520 = 1686.25；1-4月已扣 1009.50 → 676.75
    check("5月累计应纳税所得额", res.months[4].cum_taxable, "42062.50")
    check("5月预扣个税（跳档）", res.months[4].tax, "676.75")
    # 12 月累计：246000 - 60000 - 43050 - 42000 = 100950 → ×10%-2520 = 7575
    check("全年累计应纳税所得额", res.months[11].cum_taxable, "100950.00")
    check("全年月度预扣合计", res.annual_withheld, "7575.00")
    check("年度汇算应纳税额", res.settlement_tax, "7575.00")
    check("应退补税（全年无变更应为 0）", res.refund, "0.00")

    print("\n== 4. 年终奖择优（60000 元年终奖，单独 vs 并入）==")
    emp2 = Employee(employee_id="T002", name="测试2", city_code="shanghai", city_name="上海",
                    year=2026, annual_bonus_method="auto")
    months2 = [
        MonthInput(
            month=m, gross=D(20500), annual_bonus=D(60000) if m == 12 else D(0),
            contributions_override={
                "pension": D(1640), "medical": D(410), "unemployment": D("102.5"), "housing": D(1435),
            },
            deductions={"children": D(2000), "rent": D(1500)},
        )
        for m in range(1, 13)
    ]
    res2 = compute_employee(emp2, months2, policy, policy.cities["shanghai"])
    # 单独计税：7575 + 5790 = 13365 ；并入：160950×20% - 16920 = 15270 → 应选单独计税
    check("单独计税方案全年税负", res2.separate_plan_tax, "13365.00")
    check("并入综合方案全年税负", res2.merge_plan_tax, "15270.00")
    check_str("择优结果", res2.bonus_method, "separate")
    check("年终奖税额", res2.annual_bonus_tax, "5790.00")
    check("汇算应退补（年终奖单独计税不参与汇算）", res2.refund, "0.00")
    check("全年实际缴纳个税合计", res2.total_withheld, "13365.00")

    print("\n== 5. 年中入职（7 月入职，年度汇算应退税）==")
    emp3 = Employee(employee_id="T003", name="测试3", city_code="shanghai", city_name="上海",
                    year=2026, entry_month=7)
    months3 = [
        MonthInput(
            month=m, gross=D(23500),
            deductions={"rent": D(1500)},
        )
        for m in range(7, 13)
    ]
    res3 = compute_employee(emp3, months3, policy, policy.cities["shanghai"])
    # 社保基数 23500（在 7546~37731 内）：养老 1880 / 医疗 470 / 失业 117.5 / 公积金 1645 = 4112.5
    check("公积金个人（按元取整）", res3.months[0].housing, "1645")
    check("五险一金合计", res3.months[0].contributions, "4112.50")
    # 累计：141000 - 6×5000 - 6×4112.5 - 6×1500 = 77325 → ×10%-2520 = 5212.5
    check("12月累计应纳税所得额", res3.months[-1].cum_taxable, "77325.00")
    check("全年月度预扣合计", res3.annual_withheld, "5212.50")
    # 汇算按全年 60000 减除：141000-60000-24675-9000 = 47325 → 2212.50，应退 3000
    check("年度汇算应纳税额", res3.settlement_tax, "2212.50")
    check("应退税额", res3.refund, "3000.00")

    print("\n== 6. 社保公积金基数上下限（上海 2026 上半年：7460~37302 / 公积金 2690~37302）==")
    emp4 = Employee(employee_id="T004", name="测试4", city_code="shanghai", city_name="上海", year=2026)
    # 高薪 80000 → 封顶 37302；低薪 4000 → 保底 7460
    for salary, expect_social, expect_housing, tag in (
        (80000, 37302, 37302, "高薪封顶"),
        # 月薪 4000 低于社保下限 7460 → 保底；但高于公积金下限 2690 → 取实际工资
        (4000, 7460, 4000, "低薪社保保底"),
    ):
        r = compute_employee(emp4, [MonthInput(month=1, gross=D(salary))], policy, policy.cities["shanghai"])
        check(f"{tag}（月薪 {salary}）养老基数", r.months[0].pension_base, expect_social)
        check(f"{tag}（月薪 {salary}）公积金基数", r.months[0].housing_base, expect_housing)

    print("\n== 7. 闪电校验：能识别客户侧错误算法 ==")
    records = [
        {
            "employee_id": "T005", "name": "校验测试", "city": "上海", "year": 2026, "month": 1,
            "gross": D(20500),
            "pension": D(1640), "medical": D(410), "unemployment": D("102.5"), "housing": D(1435),
            "children": D(2000), "rent": D(1500),
            # 客户侧误用「按月单独计税」：8412.5 落入 10% 档 → 841.25 - 210 = 631.25
            "client_tax": D("631.25"), "client_net": D("16281.25"),
        }
    ]
    batch = run_payroll(records, policy, default_city="上海", default_year=2026)
    v = Validator(policy).validate(batch, records)
    diffs = {d.item: d.diff for d in v.diffs}
    check("个税差异被识别", diffs.get("个税"), "-378.87")
    check("实发差异被识别", diffs.get("实发工资"), "378.87")
    check_str("生成 V003 差异比对问题", any(i.code == "V003" for i in v.issues), True)

    print(f"\n{'=' * 60}\n通过 {PASS} 项，失败 {FAIL} 项\n{'=' * 60}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
