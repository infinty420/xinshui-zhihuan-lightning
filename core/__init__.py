"""
薪税智算·闪电校验 —— 内核包
===========================
policy    政策库加载（policies/*.yaml，新城市新年度零改代码）
schema    标准化工资表字段与列名别名词典
adapter   多格式工资表解析与列映射（xlsx / xls / csv，长表 / 宽表 / 单月表）
engine    薪税计算内核（累计预扣法、年度汇算、年终奖择优）
validator 闪电校验（内部勾稽 + 差异比对）
report    标准化算薪报表输出
pipeline  端到端流水线：解析 → 核算 → 校验 → 出表

算法与政策原始数据来源：GitHub 开源项目 ikiwihome/income-calc (MIT License)
"""
from .policy import Policy, PolicyError, Rates, money, D
from .schema import FIELDS, FIELD_MAP, MappingResult, build_mapping, match_field
from .adapter import SheetData, StandardizeContext, ParseReport, load_sheet, load_workbook, list_sheets, standardize
from .engine import BatchResult, Employee, MonthInput, MonthResult, YearResult, compute_employee, run_payroll
from .validator import Issue, ValidationResult, Validator
from .report import write_report
from .pipeline import run_pipeline

__all__ = [
    "Policy", "PolicyError", "Rates", "money", "D",
    "FIELDS", "FIELD_MAP", "MappingResult", "build_mapping", "match_field",
    "SheetData", "StandardizeContext", "ParseReport", "load_sheet", "load_workbook", "list_sheets", "standardize",
    "BatchResult", "Employee", "MonthInput", "MonthResult", "YearResult", "compute_employee", "run_payroll",
    "Issue", "ValidationResult", "Validator", "write_report", "run_pipeline",
]
