"""
闪电校验引擎
============
两类校验，一次跑完：

  A. 内部勾稽校验 —— 工资表自身是否自洽、计算是否合规
  B. 差异比对     —— 与客户 / 第三方既有结果逐人逐月比对

输出结构化的 Issue 清单与差异明细表，可直接作为交付附件。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable

from .policy import D, Policy, money
from .engine import BatchResult, YearResult, MonthResult
from .schema import DEDUCTION_KEYS, INCOME_KEYS

ZERO = Decimal("0")

LEVEL_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass
class Issue:
    code: str
    level: str                 # error / warning / info
    category: str              # 勾稽校验 / 差异比对
    message: str
    employee_id: str = ""
    name: str = ""
    month: int | None = None
    field: str = ""
    expected: Decimal | None = None
    actual: Decimal | None = None
    diff: Decimal | None = None

    def as_row(self) -> dict[str, Any]:
        return {
            "规则码": self.code,
            "级别": {"error": "错误", "warning": "警告", "info": "提示"}[self.level],
            "类别": self.category,
            "员工编号": self.employee_id,
            "姓名": self.name,
            "月份": self.month or "",
            "项目": self.field,
            "说明": self.message,
            "期望值": self.expected,
            "实际值": self.actual,
            "差异": self.diff,
        }


@dataclass
class DiffRow:
    employee_id: str
    name: str
    month: int
    item: str
    engine_value: Decimal
    client_value: Decimal
    diff: Decimal

    def as_row(self) -> dict[str, Any]:
        return {
            "员工编号": self.employee_id,
            "姓名": self.name,
            "月份": self.month,
            "比对项": self.item,
            "引擎重算": self.engine_value,
            "客户提供": self.client_value,
            "差异": self.diff,
            "是否一致": "一致" if abs(self.diff) <= Decimal("0.02") else "不一致",
        }


@dataclass
class ValidationResult:
    issues: list[Issue] = field(default_factory=list)
    diffs: list[DiffRow] = field(default_factory=list)

    def by_level(self, level: str) -> list[Issue]:
        return [i for i in self.issues if i.level == level]

    @property
    def error_count(self) -> int:
        return len(self.by_level("error"))

    @property
    def warning_count(self) -> int:
        return len(self.by_level("warning"))

    def summary(self) -> dict[str, int]:
        return {
            "错误": self.error_count,
            "警告": self.warning_count,
            "提示": len(self.by_level("info")),
            "比对不一致": sum(1 for d in self.diffs if abs(d.diff) > Decimal("0.02")),
        }


class Validator:
    def __init__(self, policy: Policy):
        self.policy = policy
        v = policy.validation
        self.amount_tol = D(v.get("amount_tolerance", 0.02))
        self.ratio_tol = D(v.get("ratio_tolerance", 0.0005))
        self.mom_alert = D(v.get("month_over_month_alert", 0.5))
        self.diff_error_threshold = D(v.get("diff_error_threshold", 100))
        self.housing_range = tuple(D(x) for x in v.get("housing_rate_range", [5, 12]))
        self.require_id = bool(v.get("require_id_card", False))

    # ------------------------------------------------------------------
    def validate(
        self,
        batch: BatchResult,
        records: Iterable[dict[str, Any]] | None = None,
    ) -> ValidationResult:
        result = ValidationResult()
        rec_index: dict[tuple[str, int], dict[str, Any]] = {}
        for r in records or []:
            key = (str(r.get("employee_id") or "").strip(), int(r.get("month") or 0))
            rec_index[key] = r

        self._check_duplicates(batch, result)

        for yr in batch.employees:
            self._check_identity(yr, result)
            self._check_month_continuity(yr, result)
            self._check_bases(yr, result)
            self._check_rates(yr, result)
            self._check_deductions(yr, result)
            self._check_arithmetic(yr, result)
            self._check_mom(yr, result)
            self._check_income_items(yr, rec_index, result)
            self._check_policy_notes(yr, result)
            self._compare_with_client(yr, rec_index, result)

        result.issues.sort(key=lambda i: (LEVEL_ORDER[i.level], i.code, i.employee_id, i.month or 0))
        return result

    # ---------------- A. 内部勾稽 ----------------
    def _ctx(self, yr: YearResult, m: MonthResult | None = None) -> dict[str, Any]:
        return {
            "employee_id": yr.employee.employee_id,
            "name": yr.employee.name,
            "month": m.month if m else None,
        }

    def _check_duplicates(self, batch: BatchResult, res: ValidationResult) -> None:
        seen: dict[str, int] = {}
        for yr in batch.employees:
            seen[yr.employee.employee_id] = seen.get(yr.employee.employee_id, 0) + 1
        for eid, cnt in seen.items():
            if cnt > 1:
                res.issues.append(Issue("V011", "error", "勾稽校验",
                                        f"员工编号 {eid} 出现 {cnt} 次，请确认是否重复导入",
                                        employee_id=eid))

    def _check_identity(self, yr: YearResult, res: ValidationResult) -> None:
        if not yr.employee.name:
            res.issues.append(Issue("V010", "warning", "勾稽校验", "姓名缺失",
                                    **self._ctx(yr)))
        if self.require_id and not yr.employee.id_no:
            res.issues.append(Issue("V010", "warning", "勾稽校验", "证件号码缺失，年度汇算将无法申报",
                                    **self._ctx(yr)))
        elif yr.employee.id_no and len(yr.employee.id_no) not in (15, 18):
            res.issues.append(Issue("V010", "warning", "勾稽校验",
                                    f"证件号码长度异常（{len(yr.employee.id_no)} 位）",
                                    **self._ctx(yr)))

    def _check_month_continuity(self, yr: YearResult, res: ValidationResult) -> None:
        months = [m.month for m in yr.months]
        if not months:
            return
        full = set(range(min(months), max(months) + 1))
        missing = sorted(full - set(months))
        if missing:
            res.issues.append(Issue("V009", "warning", "勾稽校验",
                                    f"缺少月份 {missing}，累计预扣可能中断，请确认是否为入职/离职或断档",
                                    **self._ctx(yr)))
        if len(months) != len(set(months)):
            res.issues.append(Issue("V009", "error", "勾稽校验", "存在重复月份，同一员工同月出现多条记录",
                                    **self._ctx(yr)))
        if len(months) < 12 and not yr.employee.entry_month and not yr.employee.exit_month:
            res.issues.append(Issue("V009", "info", "勾稽校验",
                                    f"全年仅 {len(months)} 个月数据，年度汇算口径可能不完整",
                                    **self._ctx(yr)))

    def _check_bases(self, yr: YearResult, res: ValidationResult) -> None:
        city = self.policy.cities.get(yr.employee.city_code)
        if not city:
            return
        for m in yr.months:
            period = city.period(yr.employee.year, m.month)
            for label, base, rng in (
                ("社保（养老）", m.pension_base, period.pension),
                ("社保（医疗）", m.medical_base, period.medical),
                ("社保（失业）", m.unemployment_base, period.unemployment),
                ("公积金", m.housing_base, period.housing),
            ):
                if base < rng[0] or base > rng[1]:
                    res.issues.append(Issue(
                        "V004" if "社保" in label else "V005", "warning", "勾稽校验",
                        f"{m.month}月 {label}基数 {base} 超出政策区间 [{rng[0]}, {rng[1]}]（已自动封顶）",
                        field=label + "基数", expected=rng[1], actual=base,
                        **self._ctx(yr, m)))

    def _check_rates(self, yr: YearResult, res: ValidationResult) -> None:
        city = self.policy.cities.get(yr.employee.city_code)
        if not city:
            return
        rates = yr.employee.rates or city.defaults
        low, high = self.housing_range
        if rates.housing < low or rates.housing > high:
            res.issues.append(Issue("V006", "warning", "勾稽校验",
                                    f"公积金个人比例 {rates.housing}% 超出常规区间 [{low}%, {high}%]",
                                    field="公积金比例", **self._ctx(yr)))
        for m in yr.months:
            if m.housing_base > 0:
                actual_rate = (m.housing / m.housing_base * 100).quantize(Decimal("0.01"))
                if abs(actual_rate - rates.housing) > Decimal("0.05"):
                    res.issues.append(Issue("V006", "info", "勾稽校验",
                                            f"{m.month}月公积金实缴比例约 {actual_rate}%，与配置比例 {rates.housing}% 不一致",
                                            field="公积金实缴比例", **self._ctx(yr, m)))
                    break

    def _check_deductions(self, yr: YearResult, res: ValidationResult) -> None:
        city = self.policy.cities.get(yr.employee.city_code)
        for m in yr.months:
            if m.special_deduction < 0:
                res.issues.append(Issue("V007", "error", "勾稽校验",
                                        f"{m.month}月专项附加扣除为负数", field="专项附加扣除",
                                        **self._ctx(yr, m)))
            cap_total = Decimal("0")
            for key in DEDUCTION_KEYS:
                cap = self.policy.deduction_cap(key, city)
                if cap:
                    cap_total += cap
            if cap_total and m.special_deduction > cap_total:
                res.issues.append(Issue("V007", "warning", "勾稽校验",
                                        f"{m.month}月专项附加扣除合计 {m.special_deduction} 超过常规上限 {cap_total}",
                                        field="专项附加扣除", expected=cap_total, actual=m.special_deduction,
                                        **self._ctx(yr, m)))

    def _check_arithmetic(self, yr: YearResult, res: ValidationResult) -> None:
        for m in yr.months:
            expected_net = money(m.gross + m.annual_bonus - m.contributions - m.tax - m.annual_bonus_tax)
            if abs(expected_net - m.net) > self.amount_tol:
                res.issues.append(Issue("V002", "error", "勾稽校验",
                                        f"{m.month}月实发工资勾稽不平：应发+奖金-五险一金-个税 = {expected_net}，实际 {m.net}",
                                        field="实发工资", expected=expected_net, actual=m.net,
                                        diff=money(m.net - expected_net), **self._ctx(yr, m)))
            if m.gross < 0 or m.contributions < 0 or m.tax < 0:
                res.issues.append(Issue("V012", "error", "勾稽校验",
                                        f"{m.month}月存在负值（应发 {m.gross} / 五险一金 {m.contributions} / 个税 {m.tax}）",
                                        **self._ctx(yr, m)))

    def _check_mom(self, yr: YearResult, res: ValidationResult) -> None:
        months = yr.months
        for prev, cur in zip(months, months[1:]):
            if prev.gross > 0:
                delta = abs(cur.gross - prev.gross) / prev.gross
                if delta > self.mom_alert:
                    res.issues.append(Issue("V008", "warning", "勾稽校验",
                                            f"{cur.month}月应发工资较上月变动 {delta * 100:.1f}%，请确认是否为调薪/奖金/离职结算",
                                            field="应发工资", expected=prev.gross, actual=cur.gross,
                                            diff=money(cur.gross - prev.gross), **self._ctx(yr, cur)))
            if prev.tax > 0 and cur.tax > 0:
                delta = abs(cur.tax - prev.tax) / prev.tax
                if delta > self.mom_alert * 2:
                    res.issues.append(Issue("V008", "info", "勾稽校验",
                                            f"{cur.month}月个税较上月变动 {delta * 100:.1f}%（累计预扣法下跳档属正常现象）",
                                            field="本月个税", expected=prev.tax, actual=cur.tax,
                                            diff=money(cur.tax - prev.tax), **self._ctx(yr, cur)))

    def _check_income_items(self, yr: YearResult, rec_index: dict, res: ValidationResult) -> None:
        """收入明细加总 vs 应发合计"""
        for m in yr.months:
            rec = rec_index.get((yr.employee.employee_id, m.month))
            if not rec:
                continue
            parts = [D(rec[k]) for k in INCOME_KEYS if rec.get(k) not in (None, "")]
            detail = sum((p for p in parts if p > 0), ZERO)
            # 仅当客户提供了 2 项以上收入明细时才做加总勾稽，避免部分明细造成误报
            if len(parts) >= 2 and detail > 0 and rec.get("gross") is not None:
                gross = D(rec["gross"])
                if abs(detail - gross) > self.amount_tol:
                    res.issues.append(Issue("V001", "error", "勾稽校验",
                                            f"{m.month}月收入明细合计 {money(detail)} 与应发工资 {money(gross)} 不符",
                                            field="应发工资", expected=money(detail), actual=money(gross),
                                            diff=money(gross - detail), **self._ctx(yr, m)))

    def _check_policy_notes(self, yr: YearResult, res: ValidationResult) -> None:
        for note in yr.warnings:
            res.issues.append(Issue("V014", "info", "勾稽校验", note, **self._ctx(yr)))

    # ---------------- B. 差异比对 ----------------
    def _compare_with_client(self, yr: YearResult, rec_index: dict, res: ValidationResult) -> None:
        for m in yr.months:
            rec = rec_index.get((yr.employee.employee_id, m.month))
            if not rec:
                continue
            pairs = (
                ("个税", m.tax + m.annual_bonus_tax, rec.get("client_tax")),
                ("实发工资", m.net, rec.get("client_net")),
                ("五险一金", m.contributions, rec.get("client_contributions")),
            )
            for item, engine_v, client_v in pairs:
                if client_v is None or client_v == "":
                    continue
                cv = D(client_v)
                diff = money(engine_v - cv)
                res.diffs.append(DiffRow(
                    employee_id=yr.employee.employee_id,
                    name=yr.employee.name,
                    month=m.month,
                    item=item,
                    engine_value=money(engine_v),
                    client_value=cv,
                    diff=diff,
                ))
                if abs(diff) > self.amount_tol:
                    # 按差异金额分级：小额尾差只提示，重大差异才判为错误
                    level = "error" if abs(diff) >= self.diff_error_threshold else "warning"
                    tag = "重大差异" if level == "error" else "小额差异"
                    res.issues.append(Issue(
                        "V003", level, "差异比对",
                        f"{m.month}月{item}{tag}：引擎重算 {money(engine_v)} vs 客户提供 {cv}，差异 {diff}",
                        field=item, expected=money(engine_v), actual=cv, diff=diff,
                        **self._ctx(yr, m)))
