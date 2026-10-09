"""
薪税计算内核
============
算法源自开源底座 ikiwihome/income-calc (MIT)，针对「多客户批量交付」场景做了如下扩展：

  * 月度批量：按员工 × 月份累计预扣（累计预扣预缴法）
  * 跨期接续：支持导入截至上期的累计收入 / 五险一金 / 扣除 / 已预扣税额
  * 在职区间：支持年中入职、年中离职，减除费用按实际申报月数计算
  * 年终奖：单独计税 / 并入综合所得 两种方案自动择优
  * 年度汇算：全年清算，输出应退 / 应补税额
  * 五险一金：客户给金额则直接用，未给则按基数 × 比例计算并自动 clamp 到政策上下限

计算精度：全程 Decimal，金额保留到分（公积金按元取整），不做浮点累加。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from .policy import D, Policy, Rates, money, yuan, CityPolicy
from .schema import DEDUCTION_KEYS, INCOME_KEYS

ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class Employee:
    employee_id: str = ""
    name: str = ""
    id_no: str = ""
    department: str = ""
    city_code: str = ""
    city_name: str = ""
    year: int = 0
    entry_month: int | None = None          # 年内入职月份
    exit_month: int | None = None           # 年内离职月份
    declaration_method: str = "monthly"     # monthly 月度申报 / annual 年度一次性申报
    annual_bonus_method: str = "auto"       # auto 择优 / separate 单独计税 / merge 并入综合所得
    annual_medical: Decimal = ZERO          # 大病医疗（年度汇算扣除）
    other_deduction_monthly: Decimal = ZERO
    rates: Rates | None = None              # 比例覆盖
    # 跨期接续
    prev_months: int = 0
    prev_income: Decimal = ZERO
    prev_contributions: Decimal = ZERO
    prev_special: Decimal = ZERO
    prev_other: Decimal = ZERO
    prev_withheld: Decimal = ZERO


@dataclass
class MonthInput:
    month: int
    gross: Decimal = ZERO                       # 应发工资合计（不含年终奖）
    income_items: dict[str, Decimal] = field(default_factory=dict)
    annual_bonus: Decimal = ZERO                # 当月发放的全年一次性奖金
    social_base: Decimal | None = None
    housing_base: Decimal | None = None
    contributions_override: dict[str, Decimal] = field(default_factory=dict)
    deductions: dict[str, Decimal] = field(default_factory=dict)
    other_deduction: Decimal | None = None
    client_tax: Decimal | None = None
    client_net: Decimal | None = None
    client_contributions: Decimal | None = None


@dataclass
class MonthResult:
    month: int
    gross: Decimal
    annual_bonus: Decimal
    pension_base: Decimal
    medical_base: Decimal
    unemployment_base: Decimal
    housing_base: Decimal
    pension: Decimal
    medical: Decimal
    unemployment: Decimal
    housing: Decimal
    supplemental_housing: Decimal
    contributions: Decimal
    special_deduction: Decimal
    other_deduction: Decimal
    basic_deduction: Decimal
    cum_income: Decimal
    cum_contributions: Decimal
    cum_special: Decimal
    cum_other: Decimal
    cum_basic_deduction: Decimal
    cum_taxable: Decimal
    tax_rate: Decimal
    tax: Decimal                       # 本月应预扣个税
    cum_withheld: Decimal
    annual_bonus_tax: Decimal
    net: Decimal                       # 实发
    provisional_policy: bool = False

    def as_row(self) -> dict[str, Any]:
        return {
            "月份": self.month,
            "应发工资": self.gross,
            "全年一次性奖金": self.annual_bonus,
            "养老基数": self.pension_base,
            "医疗基数": self.medical_base,
            "失业基数": self.unemployment_base,
            "公积金基数": self.housing_base,
            "养老个人": self.pension,
            "医疗个人": self.medical,
            "失业个人": self.unemployment,
            "公积金个人": self.housing,
            "补充公积金个人": self.supplemental_housing,
            "五险一金合计": self.contributions,
            "专项附加扣除": self.special_deduction,
            "其他扣除": self.other_deduction,
            "基本减除费用": self.basic_deduction,
            "累计收入": self.cum_income,
            "累计五险一金": self.cum_contributions,
            "累计专项附加扣除": self.cum_special,
            "累计其他扣除": self.cum_other,
            "累计基本减除": self.cum_basic_deduction,
            "累计应纳税所得额": self.cum_taxable,
            "适用税率": self.tax_rate,
            "本月应预扣个税": self.tax,
            "累计已预扣税额": self.cum_withheld,
            "年终奖个税": self.annual_bonus_tax,
            "实发工资": self.net,
        }


@dataclass
class YearResult:
    employee: Employee
    months: list[MonthResult]
    bonus_method: str
    annual_income: Decimal = ZERO
    annual_bonus: Decimal = ZERO
    annual_contributions: Decimal = ZERO
    annual_special: Decimal = ZERO
    annual_other: Decimal = ZERO
    annual_withheld: Decimal = ZERO
    annual_bonus_tax: Decimal = ZERO
    annual_cash: Decimal = ZERO
    settlement_taxable: Decimal = ZERO
    settlement_tax: Decimal = ZERO
    settlement_rate: Decimal = ZERO
    total_withheld: Decimal = ZERO
    refund: Decimal = ZERO             # 正数应退 / 负数应补
    final_annual_income: Decimal = ZERO
    separate_plan_tax: Decimal = ZERO  # 单独计税方案年度总税负
    merge_plan_tax: Decimal = ZERO     # 并入综合所得方案年度总税负
    warnings: list[str] = field(default_factory=list)

    def as_row(self) -> dict[str, Any]:
        return {
            "员工编号": self.employee.employee_id,
            "姓名": self.employee.name,
            "证件号码": self.employee.id_no,
            "部门": self.employee.department,
            "参保城市": self.employee.city_name,
            "年度": self.employee.year,
            "申报月数": len(self.months),
            "年度收入": self.annual_income,
            "全年一次性奖金": self.annual_bonus,
            "年终奖计税方式": {"separate": "单独计税", "merge": "并入综合所得", "auto": "自动择优"}.get(
                self.employee.annual_bonus_method, self.employee.annual_bonus_method) + f"（实取：{self.bonus_method}）",
            "年度五险一金": self.annual_contributions,
            "年度专项附加扣除": self.annual_special,
            "年度其他扣除": self.annual_other,
            "年度已预扣个税": self.annual_withheld,
            "年终奖个税": self.annual_bonus_tax,
            "年度实发合计": self.annual_cash,
            "汇算应纳税所得额": self.settlement_taxable,
            "汇算适用税率": self.settlement_rate,
            "汇算应纳税额": self.settlement_tax,
            "已预扣合计": self.total_withheld,
            "应退补税额(正退负补)": self.refund,
            "全年税后收入": self.final_annual_income,
        }


@dataclass
class BatchResult:
    employees: list[YearResult]
    skipped: list[dict[str, Any]] = field(default_factory=list)
    city_name: str = ""
    year: int = 0


# ---------------------------------------------------------------------------
# 五险一金
# ---------------------------------------------------------------------------
def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    if value is None:
        return lower
    return max(lower, min(value, upper))


def compute_contributions(
    month_input: MonthInput,
    city: CityPolicy,
    year: int,
    rates: Rates,
) -> dict[str, Decimal]:
    """计算当月五险一金（个人部分）

    优先级：客户给定金额 > 客户给定合计 > 按基数 × 比例推算
    """
    period = city.period(year, month_input.month)
    ov = month_input.contributions_override

    detail_keys = ("pension", "medical", "unemployment", "housing")
    if all(ov.get(k) is not None for k in detail_keys):
        pension = money(ov["pension"])
        medical = money(ov["medical"])
        unemployment = money(ov["unemployment"])
        housing = money(ov["housing"])
        supplemental = money(ov.get("supplemental_housing") or 0)
        # 补齐未提供的基数（用于报表与校验展示）
        src_social = month_input.social_base if month_input.social_base is not None else month_input.gross
        src_housing = month_input.housing_base if month_input.housing_base is not None else month_input.gross
        return {
            "pension_base": money(_clamp(D(src_social), *period.pension)),
            "medical_base": money(_clamp(D(src_social), *period.medical)),
            "unemployment_base": money(_clamp(D(src_social), *period.unemployment)),
            "housing_base": money(_clamp(D(src_housing), *period.housing)),
            "pension": pension, "medical": medical, "unemployment": unemployment,
            "housing": housing, "supplemental_housing": supplemental,
            "contributions": money(pension + medical + unemployment + housing + supplemental),
            "provisional": period.provisional,
        }

    social_src = month_input.social_base if month_input.social_base is not None else month_input.gross
    housing_src = month_input.housing_base if month_input.housing_base is not None else month_input.gross

    pension_base = money(_clamp(D(social_src), *period.pension))
    medical_base = money(_clamp(D(social_src), *period.medical))
    unemployment_base = money(_clamp(D(social_src), *period.unemployment))
    housing_base = money(_clamp(D(housing_src), *period.housing))

    pension = money(pension_base * rates.pension / Decimal("100"))
    medical = money(medical_base * rates.medical / Decimal("100") + rates.medical_fixed)
    unemployment = money(unemployment_base * rates.unemployment / Decimal("100"))
    housing = yuan(housing_base * rates.housing / Decimal("100"))
    supplemental = yuan(housing_base * rates.supplemental_housing / Decimal("100"))

    total = pension + medical + unemployment + housing + supplemental
    if ov.get("contributions") is not None:
        total = money(ov["contributions"])

    return {
        "pension_base": pension_base, "medical_base": medical_base,
        "unemployment_base": unemployment_base, "housing_base": housing_base,
        "pension": pension, "medical": medical, "unemployment": unemployment,
        "housing": housing, "supplemental_housing": supplemental,
        "contributions": money(total),
        "provisional": period.provisional,
    }


# ---------------------------------------------------------------------------
# 单员工年度计算
# ---------------------------------------------------------------------------
def _month_count(emp: Employee, month: int, index: int) -> int:
    """截至当月的累计申报月数（决定 5000 元/月 减除费用总额）"""
    if emp.entry_month:
        return max(1, month - emp.entry_month + 1)
    return index + max(0, emp.prev_months)


def _run_year(
    emp: Employee,
    inputs: list[MonthInput],
    policy: Policy,
    city: CityPolicy,
    bonus_method: str,
) -> YearResult:
    """按指定年终奖方案跑一遍全年"""
    monthly_declare = emp.declaration_method == "monthly"
    # separate 方案：年终奖单独计税，不计入综合所得累计
    separate = bonus_method == "separate"

    cum_income = D(emp.prev_income)
    cum_contrib = D(emp.prev_contributions)
    cum_special = D(emp.prev_special)
    cum_other = D(emp.prev_other)
    cum_withheld = D(emp.prev_withheld)

    results: list[MonthResult] = []
    annual_bonus_total = ZERO
    annual_bonus_tax_total = ZERO
    warnings: list[str] = []

    for idx, mi in enumerate(inputs, start=1):
        month = mi.month
        gross = money(max(ZERO, D(mi.gross)))
        bonus = money(max(ZERO, D(mi.annual_bonus)))

        contrib = compute_contributions(mi, city, emp.year, emp.rates or city.defaults)

        # 专项附加扣除
        special = ZERO
        for k in DEDUCTION_KEYS:
            v = mi.deductions.get(k)
            if v is not None:
                special += max(ZERO, D(v))
        if mi.deductions.get("special_deduction_total") is not None:
            special = max(ZERO, D(mi.deductions["special_deduction_total"]))
        special = money(special)

        other = money(max(ZERO, D(mi.other_deduction if mi.other_deduction is not None
                                   else emp.other_deduction_monthly)))

        # 合并方案的年终奖计入当月综合所得
        income_this_month = gross if separate else gross + bonus

        cum_income = money(cum_income + income_this_month)
        cum_contrib = money(cum_contrib + contrib["contributions"])
        claimed_special = special if monthly_declare else ZERO
        cum_special = money(cum_special + claimed_special)
        cum_other = money(cum_other + other)

        months_count = _month_count(emp, month, idx)
        cum_basic = money(policy.monthly_basic_deduction * months_count)

        cum_taxable = max(ZERO, money(
            cum_income - cum_basic - cum_contrib - cum_special - cum_other
        ))
        cum_tax, rate = policy.tax_of(cum_taxable)
        tax = money(max(ZERO, cum_tax - cum_withheld))
        cum_withheld = money(cum_withheld + tax)

        # 年终奖单独计税
        bonus_tax = ZERO
        if separate and bonus > 0:
            bonus_tax, _ = policy.annual_bonus_tax(bonus)

        annual_bonus_total += bonus
        annual_bonus_tax_total += bonus_tax

        net = money(gross + bonus - contrib["contributions"] - tax - bonus_tax)

        results.append(MonthResult(
            month=month,
            gross=gross,
            annual_bonus=bonus,
            pension_base=contrib["pension_base"],
            medical_base=contrib["medical_base"],
            unemployment_base=contrib["unemployment_base"],
            housing_base=contrib["housing_base"],
            pension=contrib["pension"],
            medical=contrib["medical"],
            unemployment=contrib["unemployment"],
            housing=contrib["housing"],
            supplemental_housing=contrib["supplemental_housing"],
            contributions=contrib["contributions"],
            special_deduction=special,
            other_deduction=other,
            basic_deduction=policy.monthly_basic_deduction,
            cum_income=cum_income,
            cum_contributions=cum_contrib,
            cum_special=cum_special,
            cum_other=cum_other,
            cum_basic_deduction=cum_basic,
            cum_taxable=cum_taxable,
            tax_rate=rate,
            tax=tax,
            cum_withheld=cum_withheld,
            annual_bonus_tax=bonus_tax,
            net=net,
            provisional_policy=bool(contrib["provisional"]),
        ))
        if contrib["provisional"]:
            warnings.append(f"{month}月沿用未正式公布口径（暂行）")

    # ---------------- 年度汇算 ----------------
    annual_income = money(sum((m.gross for m in results), ZERO))
    annual_contrib = money(sum((m.contributions for m in results), ZERO))
    annual_special = money(sum((m.special_deduction for m in results), ZERO))
    annual_other = money(sum((m.other_deduction for m in results), ZERO))
    annual_withheld = money(sum((m.tax for m in results), ZERO))
    annual_cash = money(sum((m.net for m in results), ZERO))

    medical_ded = ZERO
    threshold = D(policy.medical_deduction.get("threshold", 15000))
    cap = D(policy.medical_deduction.get("annual_cap", 80000))
    if emp.annual_medical > threshold:
        medical_ded = min(cap, emp.annual_medical - threshold)

    base_amount = D(emp.prev_income)   # 汇算口径：含接续部分需一并计入，故用累计值
    settlement_income = max(cum_income, annual_income)
    settlement_contrib = max(cum_contrib, annual_contrib)
    settlement_special = max(cum_special, annual_special) if monthly_declare else annual_special
    settlement_other = max(cum_other, annual_other)

    settlement_taxable = max(ZERO, money(
        settlement_income - policy.annual_basic_deduction - settlement_contrib
        - settlement_special - settlement_other - medical_ded
    ))
    settlement_tax, s_rate = policy.tax_of(settlement_taxable)

    # 全年实际缴纳个税合计（月度预扣 + 年终奖税）
    total_withheld = money(annual_withheld + annual_bonus_tax_total)
    # 汇算退补只针对综合所得：年终奖单独计税的税款不并入汇算，不退不补
    refund = money(annual_withheld - settlement_tax)    # 正=应退，负=应补

    return YearResult(
        employee=emp,
        months=results,
        bonus_method=bonus_method,
        annual_income=annual_income,
        annual_bonus=money(annual_bonus_total),
        annual_contributions=annual_contrib,
        annual_special=annual_special,
        annual_other=annual_other,
        annual_withheld=annual_withheld,
        annual_bonus_tax=money(annual_bonus_tax_total),
        annual_cash=annual_cash,
        settlement_taxable=settlement_taxable,
        settlement_tax=settlement_tax,
        settlement_rate=s_rate,
        total_withheld=total_withheld,
        refund=refund,
        final_annual_income=money(annual_cash + refund),
        warnings=sorted(set(warnings)),
    )


def compute_employee(emp: Employee, inputs: list[MonthInput], policy: Policy, city: CityPolicy) -> YearResult:
    """计算单个员工全年薪税；年终奖方案按配置自动择优"""
    ordered = sorted(inputs, key=lambda x: x.month)
    has_bonus = any(D(i.annual_bonus) > 0 for i in ordered)

    if not has_bonus or emp.annual_bonus_method == "separate":
        return _run_year(emp, ordered, policy, city, "separate")
    if emp.annual_bonus_method == "merge":
        return _run_year(emp, ordered, policy, city, "merge")

    # auto：两方案都跑，比较「全年实际税负」
    #   单独计税 = 综合所得汇算税额 + 年终奖单独计税税额
    #   并入综合 = 含年终奖的汇算税额
    sep = _run_year(emp, ordered, policy, city, "separate")
    mrg = _run_year(emp, ordered, policy, city, "merge")
    sep_total = sep.settlement_tax + sep.annual_bonus_tax
    mrg_total = mrg.settlement_tax
    winner = sep if sep_total <= mrg_total else mrg
    loser_total = mrg_total if winner is sep else sep_total
    winner.separate_plan_tax = money(sep.settlement_tax + sep.annual_bonus_tax)
    winner.merge_plan_tax = money(mrg.settlement_tax)
    winner.warnings = sorted(set(winner.warnings + [
        f"年终奖已自动择优：{'单独计税' if winner is sep else '并入综合所得'}"
        f"（另一方案全年税负 {loser_total} 元，本方案 {sep_total if winner is sep else mrg_total} 元）"
    ]))
    return winner


# ---------------------------------------------------------------------------
# 批量入口
# ---------------------------------------------------------------------------
def run_payroll(
    records: Iterable[dict[str, Any]],
    policy: Policy,
    default_city: str | None = None,
    default_year: int | None = None,
    bonus_method: str = "auto",
) -> BatchResult:
    """批量核算：records 为标准化后的记录（每行 = 员工 × 月）"""
    grouped: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, Any]] = []

    for rec in records:
        eid = str(rec.get("employee_id") or "").strip()
        if not eid:
            skipped.append({"reason": "缺少员工编号", "record": rec})
            continue
        g = grouped.setdefault(eid, {"meta": None, "months": []})
        if g["meta"] is None:
            g["meta"] = rec
        g["months"].append(rec)

    results: list[YearResult] = []
    city_name = ""
    year_out = 0

    for eid, bundle in grouped.items():
        meta = bundle["meta"]
        try:
            city = policy.resolve_city(meta.get("city"), default_city)
        except Exception as exc:                      # noqa: BLE001
            skipped.append({"reason": str(exc), "record": meta})
            continue

        year = int(meta.get("year") or default_year or 0)
        if not year:
            skipped.append({"reason": "缺少核算年度", "record": meta})
            continue
        if year not in city.years:
            skipped.append({
                "reason": f"{city.name} 尚未配置 {year} 年度政策（可用：{city.supported_years()}）",
                "record": meta,
            })
            continue

        city_name = city.name
        year_out = year

        emp = Employee(
            employee_id=eid,
            name=str(meta.get("name") or "").strip(),
            id_no=str(meta.get("id_no") or "").strip(),
            department=str(meta.get("department") or "").strip(),
            city_code=city.code,
            city_name=city.name,
            year=year,
            entry_month=_as_int(meta.get("entry_month")),
            exit_month=_as_int(meta.get("exit_month")),
            declaration_method=str(meta.get("declaration_method") or "monthly"),
            annual_bonus_method=str(meta.get("annual_bonus_method") or bonus_method),
            annual_medical=D(meta.get("annual_medical")),
            other_deduction_monthly=D(meta.get("other_deduction")),
            rates=_build_rates(meta, city.defaults),
            prev_months=_as_int(meta.get("prev_months")) or 0,
            prev_income=D(meta.get("prev_income")),
            prev_contributions=D(meta.get("prev_contributions")),
            prev_special=D(meta.get("prev_special")),
            prev_other=D(meta.get("prev_other")),
            prev_withheld=D(meta.get("prev_withheld")),
        )

        month_inputs: list[MonthInput] = []
        for rec in bundle["months"]:
            month = _as_int(rec.get("month"))
            if not month or not (1 <= month <= 12):
                skipped.append({"reason": f"月份无效：{rec.get('month')}", "record": rec})
                continue
            gross = rec.get("gross")
            if gross is None or D(gross) == 0:
                gross = sum((D(rec.get(k) or 0) for k in INCOME_KEYS), ZERO)
            month_inputs.append(MonthInput(
                month=month,
                gross=D(gross),
                income_items={k: D(rec.get(k) or 0) for k in INCOME_KEYS},
                annual_bonus=D(rec.get("annual_bonus")),
                social_base=_opt(rec.get("social_base")),
                housing_base=_opt(rec.get("housing_base")),
                contributions_override={
                    k: _opt(rec.get(k)) for k in
                    ("pension", "medical", "unemployment", "housing", "supplemental_housing", "contributions")
                },
                deductions={k: _opt(rec.get(k)) for k in (*DEDUCTION_KEYS, "special_deduction_total")},
                other_deduction=_opt(rec.get("other_deduction")),
                client_tax=_opt(rec.get("client_tax")),
                client_net=_opt(rec.get("client_net")),
                client_contributions=_opt(rec.get("client_contributions")),
            ))

        if not month_inputs:
            skipped.append({"reason": "无有效月度数据", "record": meta})
            continue

        results.append(compute_employee(emp, month_inputs, policy, city))

    results.sort(key=lambda r: r.employee.employee_id)
    return BatchResult(employees=results, skipped=skipped, city_name=city_name, year=year_out)


def _as_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _opt(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return D(value)


def _build_rates(meta: dict, defaults: Rates) -> Rates | None:
    keys = {
        "pension": "pension_rate",
        "medical": "medical_rate",
        "unemployment": "unemployment_rate",
        "housing": "housing_rate",
        "supplemental_housing": "supplemental_housing_rate",
    }
    patch = {k: meta[v] for k, v in keys.items() if meta.get(v) not in (None, "")}
    if not patch:
        return None
    return Rates.from_dict(patch, defaults)
