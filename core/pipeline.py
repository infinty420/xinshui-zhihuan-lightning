"""
端到端流水线
============
解析 → 标准化 → 批量核算 → 闪电校验 → 输出标准化算薪报表

CLI 与 Web 共用同一条流水线，保证两种入口结果完全一致。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .adapter import StandardizeContext, load_sheet, standardize
from .engine import BatchResult, run_payroll
from .policy import Policy
from .report import write_report
from .schema import MappingResult, build_mapping
from .validator import ValidationResult, Validator


@dataclass
class RunOptions:
    input_path: str
    out_path: str | None = None
    sheet: str | None = None
    city: str | None = None
    year: int | None = None
    month: int | None = None
    bonus_method: str = "auto"          # auto / separate / merge
    declaration_method: str = "monthly"
    client: str = ""
    mapping_override: dict[str, str] | None = None   # 标准字段 -> 原始表头
    defaults: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunOutcome:
    batch: BatchResult
    validation: ValidationResult
    mapping: MappingResult
    records: list[dict[str, Any]]
    report_path: Path | None
    notes: list[str] = field(default_factory=list)
    sheet_name: str = ""
    layout: str = ""
    generated_at: str = ""

    def summary(self) -> dict[str, Any]:
        s = self.validation.summary()
        s.update({
            "员工人数": len(self.batch.employees),
            "记录条数": len(self.records),
            "年度": self.batch.year,
            "城市": self.batch.city_name,
            "表形态": {"long": "长表", "wide": "宽表", "single": "单月表"}.get(self.layout, self.layout),
            "跳过记录": len(self.batch.skipped),
        })
        return s


def run_pipeline(options: RunOptions, policy: Policy | None = None) -> RunOutcome:
    policy = policy or Policy.load()

    sheet_data = load_sheet(options.input_path, options.sheet)
    if not sheet_data.headers:
        raise ValueError("未能从文件中识别出表头，请检查文件格式或指定 sheet")

    mapping = build_mapping(sheet_data.headers)
    if options.mapping_override:
        mapping.mapping.update({k: v for k, v in options.mapping_override.items() if v})
        mapping.missing = [f for f in mapping.missing if f not in mapping.mapping]

    ctx = StandardizeContext(
        year=options.year,
        month=options.month,
        city=options.city,
        bonus_method=options.bonus_method,
        declaration_method=options.declaration_method,
        defaults=options.defaults,
    )
    records, parse_report = standardize(sheet_data, ctx, mapping)

    if not records:
        raise ValueError("未解析出任何有效记录，请检查列映射与数据内容")

    batch = run_payroll(
        records, policy,
        default_city=options.city,
        default_year=options.year,
        bonus_method=options.bonus_method,
    )
    if not batch.employees:
        raise ValueError(
            "没有可核算的员工，常见原因：缺少员工编号 / 未指定参保城市 / 该年度政策未配置。"
            f"跳过明细：{batch.skipped[:3]}"
        )

    validation = Validator(policy).validate(batch, records)

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_path = None
    if options.out_path:
        report_path = write_report(
            batch, validation, policy, options.out_path,
            meta={
                "client": options.client,
                "source_file": Path(options.input_path).name,
                "generated_at": generated_at,
                "year": options.year,
            },
        )

    return RunOutcome(
        batch=batch,
        validation=validation,
        mapping=mapping,
        records=records,
        report_path=report_path,
        notes=parse_report.notes,
        sheet_name=sheet_data.name,
        layout=parse_report.layout,
        generated_at=generated_at,
    )


@dataclass
class EntityPlan:
    """集团某一家经营主体的核算任务"""
    entity: str                       # 主体名称
    input_path: str
    city: str | None = None
    sheet: str | None = None
    month: int | None = None


@dataclass
class GroupOutcome:
    """集团级批量结果"""
    entity_outcomes: list[tuple[str, RunOutcome]] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)

    def combined_summary(self) -> dict[str, Any]:
        total_emp = sum(len(o.batch.employees) for _, o in self.entity_outcomes)
        total_rec = sum(len(o.records) for _, o in self.entity_outcomes)
        err = sum(o.validation.error_count for _, o in self.entity_outcomes)
        warn = sum(o.validation.warning_count for _, o in self.entity_outcomes)
        diff = sum(o.validation.summary()["比对不一致"] for _, o in self.entity_outcomes)
        return {
            "主体数": len(self.entity_outcomes),
            "失败主体数": len(self.errors),
            "员工人数": total_emp,
            "记录条数": total_rec,
            "错误": err,
            "警告": warn,
            "比对不一致": diff,
        }


def run_group(
    plans: list[EntityPlan],
    out_dir: str | Path,
    options_base: RunOptions | None = None,
    policy: Policy | None = None,
) -> GroupOutcome:
    """集团级批量核算：逐主体跑同一套流水线，结果各自出表。

    18 家主体报表模板不一致时，只需给出各自的文件路径，
    适配层会自动识别表头与表形态，无需为每家写代码。
    """
    policy = policy or Policy.load()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    outcome = GroupOutcome()

    for plan in plans:
        out_path = out_dir / f"{plan.entity}_算薪报表.xlsx"
        opts = RunOptions(
            input_path=plan.input_path,
            out_path=str(out_path),
            sheet=plan.sheet,
            city=plan.city,
            year=(options_base.year if options_base else None),
            month=plan.month if plan.month is not None else (options_base.month if options_base else None),
            bonus_method=options_base.bonus_method if options_base else "auto",
            declaration_method=options_base.declaration_method if options_base else "monthly",
            client=plan.entity,
            defaults=options_base.defaults if options_base else {},
        )
        try:
            outcome.entity_outcomes.append((plan.entity, run_pipeline(opts, policy)))
        except Exception as exc:                              # noqa: BLE001
            outcome.errors.append((plan.entity, f"{type(exc).__name__}: {exc}"))
    return outcome
