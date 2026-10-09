"""
标准化算薪报表输出
==================
输出一份可直接交付给客户的 Excel 工作簿，包含 7 个标准化 Sheet：

  1. 算薪明细表      —— 逐人逐月全字段（应发 / 五险一金 / 扣除 / 累计预扣 / 实发）
  2. 月度汇总表      —— 按月的部门级汇总
  3. 年度汇总表      —— 逐人年度累计 + 汇算清缴 + 应退补税
  4. 社保公积金明细表 —— 基数与各项缴纳额，便于与社保系统核对
  5. 异常校验清单    —— 闪电校验输出的错误 / 警告 / 提示
  6. 差异比对表      —— 引擎重算 vs 客户既有结果（仅当客户提供了基准列时生成）
  7. 政策参数与说明  —— 本次计算所依据的政策口径，交付可追溯
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .engine import BatchResult, YearResult
from .policy import Policy
from .validator import ValidationResult

# ---------------------------------------------------------------------------
# 样式（浅色主题）
# ---------------------------------------------------------------------------
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(color="FFFFFF", bold=True, size=10)
TITLE_FONT = Font(bold=True, size=13, color="1F4E79")
SECTION_FILL = PatternFill("solid", fgColor="DCE6F1")
WARN_FILL = PatternFill("solid", fgColor="FFF2CC")
ERROR_FILL = PatternFill("solid", fgColor="F8CBAD")
INFO_FILL = PatternFill("solid", fgColor="E2EFDA")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
MONEY_FMT = "#,##0.00"
RATE_FMT = "0.00%"

LEVEL_FILL = {"error": ERROR_FILL, "warning": WARN_FILL, "info": INFO_FILL}


def _num(value) -> float | str | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return value


def _write_table(
    ws,
    headers: list[str],
    rows: Iterable[dict[str, Any]],
    money_cols: set[str] | None = None,
    rate_cols: set[str] | None = None,
    freeze: str = "A2",
    start_row: int = 1,
) -> int:
    money_cols = money_cols or set()
    rate_cols = rate_cols or set()

    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=start_row, column=c, value=h)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER

    r = start_row
    for data in rows:
        r += 1
        for c, h in enumerate(headers, start=1):
            v = _num(data.get(h))
            cell = ws.cell(row=r, column=c, value=v)
            cell.border = BORDER
            if h in money_cols:
                cell.number_format = MONEY_FMT
            elif h in rate_cols:
                cell.number_format = RATE_FMT

    # 列宽
    for c, h in enumerate(headers, start=1):
        width = max(len(str(h)) * 2.0 + 2, 10)
        sample = [str(_num(d.get(h)) or "") for d in list(rows)[:50]] if rows else []
        if sample:
            width = max(width, min(30, max(len(s) for s in sample) + 4))
        ws.column_dimensions[get_column_letter(c)].width = min(width, 34)

    ws.freeze_panes = freeze
    return r


def _border_sheet(ws, max_row: int, max_col: int) -> None:
    for r in range(1, max_row + 1):
        for c in range(1, max_col + 1):
            ws.cell(row=r, column=c).border = BORDER


# ---------------------------------------------------------------------------
# 各 Sheet
# ---------------------------------------------------------------------------
def _sheet_detail(wb: Workbook, batch: BatchResult) -> None:
    ws = wb.create_sheet("算薪明细表")
    headers: list[str] = []
    rows: list[dict[str, Any]] = []
    for yr in batch.employees:
        for m in yr.months:
            row = {
                "员工编号": yr.employee.employee_id,
                "姓名": yr.employee.name,
                "部门": yr.employee.department,
                "参保城市": yr.employee.city_name,
                "年度": yr.employee.year,
            }
            row.update(m.as_row())
            rows.append(row)
            if not headers:
                headers = list(row.keys())
    money_cols = {h for h in headers if h not in ("员工编号", "姓名", "部门", "参保城市", "年度", "月份", "适用税率")}
    _write_table(ws, headers, rows, money_cols=money_cols, rate_cols={"适用税率"}, freeze="F2")


def _sheet_monthly_summary(wb: Workbook, batch: BatchResult) -> None:
    ws = wb.create_sheet("月度汇总表")
    buckets: dict[int, dict[str, Decimal]] = {}
    for yr in batch.employees:
        for m in yr.months:
            b = buckets.setdefault(m.month, {
                "人数": Decimal(0), "应发工资": Decimal("0"), "全年一次性奖金": Decimal("0"),
                "五险一金": Decimal("0"), "专项附加扣除": Decimal("0"),
                "本月应预扣个税": Decimal("0"), "年终奖个税": Decimal("0"), "实发工资": Decimal("0"),
            })
            b["人数"] += 1
            b["应发工资"] += m.gross
            b["全年一次性奖金"] += m.annual_bonus
            b["五险一金"] += m.contributions
            b["专项附加扣除"] += m.special_deduction
            b["本月应预扣个税"] += m.tax
            b["年终奖个税"] += m.annual_bonus_tax
            b["实发工资"] += m.net

    headers = ["月份", "人数", "应发工资", "全年一次性奖金", "五险一金", "专项附加扣除",
               "本月应预扣个税", "年终奖个税", "实发工资"]
    rows = [{"月份": k, **{h: v for h, v in b.items()}} for k, b in sorted(buckets.items())]
    for r in rows:
        r["人数"] = int(r["人数"])
    _write_table(ws, headers, rows, money_cols=set(headers) - {"月份", "人数"})


def _sheet_yearly_summary(wb: Workbook, batch: BatchResult) -> None:
    ws = wb.create_sheet("年度汇总表")
    headers: list[str] = []
    rows: list[dict[str, Any]] = []
    for yr in batch.employees:
        row = yr.as_row()
        row["年终奖方案对比(单独计税)"] = yr.separate_plan_tax
        row["年终奖方案对比(并入综合)"] = yr.merge_plan_tax
        rows.append(row)
        if not headers:
            headers = list(row.keys())
    skip = {"员工编号", "姓名", "证件号码", "部门", "参保城市", "年度", "申报月数", "年终奖计税方式", "汇算适用税率"}
    _write_table(ws, headers, rows, money_cols=set(headers) - skip, freeze="C2")


def _sheet_insurance(wb: Workbook, batch: BatchResult) -> None:
    ws = wb.create_sheet("社保公积金明细表")
    headers = ["员工编号", "姓名", "参保城市", "年度", "月份", "养老基数", "医疗基数", "失业基数",
               "公积金基数", "养老个人", "医疗个人", "失业个人", "公积金个人", "补充公积金个人", "五险一金合计"]
    rows: list[dict[str, Any]] = [
        {
            "员工编号": yr.employee.employee_id, "姓名": yr.employee.name,
            "参保城市": yr.employee.city_name, "年度": yr.employee.year,
            "月份": m.month, "养老基数": m.pension_base, "医疗基数": m.medical_base,
            "失业基数": m.unemployment_base, "公积金基数": m.housing_base,
            "养老个人": m.pension, "医疗个人": m.medical, "失业个人": m.unemployment,
            "公积金个人": m.housing, "补充公积金个人": m.supplemental_housing,
            "五险一金合计": m.contributions,
        }
        for yr in batch.employees for m in yr.months
    ]
    _write_table(ws, headers, rows, money_cols=set(headers) - {"员工编号", "姓名", "参保城市", "年度", "月份"},
                 freeze="F2")


def _sheet_issues(wb: Workbook, validation: ValidationResult) -> None:
    ws = wb.create_sheet("异常校验清单")
    headers = ["规则码", "级别", "类别", "员工编号", "姓名", "月份", "项目", "说明", "期望值", "实际值", "差异"]
    rows = [i.as_row() for i in validation.issues] or [
        {"规则码": "-", "级别": "提示", "类别": "-", "员工编号": "-", "姓名": "-", "月份": "",
         "项目": "-", "说明": "未发现异常", "期望值": None, "实际值": None, "差异": None}
    ]
    last = _write_table(ws, headers, rows, money_cols={"期望值", "实际值", "差异"})
    for r in range(2, last + 1):
        level = ws.cell(row=r, column=2).value
        fill = LEVEL_FILL.get({"错误": "error", "警告": "warning", "提示": "info"}.get(level, "info"))
        for c in range(1, len(headers) + 1):
            ws.cell(row=r, column=c).fill = fill
    ws.column_dimensions["H"].width = 60


def _sheet_diff(wb: Workbook, validation: ValidationResult) -> None:
    ws = wb.create_sheet("差异比对表")
    headers = ["员工编号", "姓名", "月份", "比对项", "引擎重算", "客户提供", "差异", "是否一致"]
    rows = [d.as_row() for d in validation.diffs] or [
        {"员工编号": "-", "姓名": "-", "月份": "", "比对项": "未提供客户基准数据",
         "引擎重算": None, "客户提供": None, "差异": None, "是否一致": "-"}
    ]
    last = _write_table(ws, headers, rows, money_cols={"引擎重算", "客户提供", "差异"})
    for r in range(2, last + 1):
        if ws.cell(row=r, column=8).value == "不一致":
            for c in range(1, len(headers) + 1):
                ws.cell(row=r, column=c).fill = ERROR_FILL


def _sheet_policy(wb: Workbook, batch: BatchResult, policy: Policy, meta: dict[str, Any]) -> None:
    ws = wb.create_sheet("政策参数与说明")
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 26
    ws.column_dimensions["C"].width = 26
    ws.column_dimensions["D"].width = 26
    ws.column_dimensions["E"].width = 26

    r = 1
    ws.cell(row=r, column=1, value="薪税智算·闪电校验 —— 算薪报表").font = TITLE_FONT
    r += 2

    def kv(key: str, value: Any) -> None:
        nonlocal r
        c1 = ws.cell(row=r, column=1, value=key)
        c1.fill = SECTION_FILL
        c1.font = Font(bold=True)
        c1.border = BORDER
        c2 = ws.cell(row=r, column=2, value=value)
        c2.border = BORDER
        r += 1

    kv("客户/项目名称", meta.get("client", ""))
    kv("核算年度", batch.year or meta.get("year", ""))
    kv("参保城市", batch.city_name)
    kv("员工人数", len(batch.employees))
    kv("生成时间", meta.get("generated_at", ""))
    kv("源文件", meta.get("source_file", ""))
    kv("基本减除费用", f"{policy.monthly_basic_deduction} 元/月（{policy.annual_basic_deduction} 元/年）")
    kv("年终奖政策", f"单独计税有效期至 {policy.bonus_policy.get('separate_tax_valid_until', '2027-12-31')}")
    r += 1

    ws.cell(row=r, column=1, value="综合所得年度税率表").font = Font(bold=True)
    r += 1
    for h, c in zip(["级距上限(元)", "税率", "速算扣除数"], range(1, 4)):
        cell = ws.cell(row=r, column=c, value=h)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.border = BORDER
    r += 1
    for b in policy.tax_brackets:
        ws.cell(row=r, column=1, value=float(b.upper) if b.upper is not None else "以上").border = BORDER
        c = ws.cell(row=r, column=2, value=float(b.rate))
        c.number_format = "0%"
        c.border = BORDER
        ws.cell(row=r, column=3, value=float(b.quick)).border = BORDER
        r += 1
    r += 1

    ws.cell(row=r, column=1, value="本次适用城市政策").font = Font(bold=True)
    r += 1
    cities_used = {yr.employee.city_code for yr in batch.employees}
    for code in sorted(cities_used):
        city = policy.cities[code]
        for h, c in zip(["半年度区间", "养老基数", "医疗基数", "失业基数", "公积金基数", "备注"], range(1, 7)):
            cell = ws.cell(row=r, column=c, value=h)
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.border = BORDER
        r += 1
        for period in city.years.get(batch.year, []):
            ws.cell(row=r, column=1, value=f"{city.name} {batch.year}年 {period.months[0]}-{period.months[1]}月").border = BORDER
            for i, rng in enumerate((period.pension, period.medical, period.unemployment, period.housing), start=2):
                ws.cell(row=r, column=i, value=f"{rng[0]} ~ {rng[1]}").border = BORDER
            ws.cell(row=r, column=6, value="暂行口径" if period.provisional else "").border = BORDER
            r += 1
    r += 1

    ws.cell(row=r, column=1, value="专项附加扣除标准（元/月）").font = Font(bold=True)
    r += 1
    for key, item in policy.special_deductions.items():
        cap = item.get("monthly_cap")
        text = f"{float(cap)} / {item.get('unit', '')}" if cap else "按城市分档"
        kv(item.get("label", key), text)
    r += 1

    ws.cell(row=r, column=1, value="声明").font = Font(bold=True)
    r += 1
    ws.cell(row=r, column=1, value=(
        "本表由「薪税智算·闪电校验」按累计预扣预缴法自动计算，政策口径源自开源项目 ikiwihome/income-calc (MIT) "
        "并以 policies/ 下配置为准。社保公积金基数执行各地公布上下限，标注为「暂行口径」的区间沿用最近已公布标准。"
        "本结果供薪酬核算参考，实际申报以主管税务机关与社保经办机构为准。"
    ))
    ws.merge_cells(start_row=r, start_column=1, end_row=r + 2, end_column=5)
    ws.cell(row=r, column=1).alignment = Alignment(wrap_text=True, vertical="top")


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def write_report(
    batch: BatchResult,
    validation: ValidationResult,
    policy: Policy,
    out_path: str | Path,
    meta: dict[str, Any] | None = None,
) -> Path:
    meta = meta or {}
    wb = Workbook()
    wb.remove(wb.active)

    _sheet_detail(wb, batch)
    _sheet_monthly_summary(wb, batch)
    _sheet_yearly_summary(wb, batch)
    _sheet_insurance(wb, batch)
    _sheet_issues(wb, validation)
    _sheet_diff(wb, validation)
    _sheet_policy(wb, batch, policy, meta)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out
