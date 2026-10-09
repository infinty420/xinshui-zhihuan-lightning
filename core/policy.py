"""
政策库加载器
===========
从 policies/national.yaml 与 policies/cities.yaml 读取政策参数。
新增城市 / 新增年度只需编辑 YAML，无需改动任何代码。

原始政策数据来源：GitHub 开源项目 ikiwihome/income-calc (MIT License)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import yaml

POLICY_DIR = Path(__file__).resolve().parent.parent / "policies"


def D(value) -> Decimal:
    """转 Decimal，空值按 0 处理"""
    if value is None or value == "":
        return Decimal("0")
    return Decimal(str(value))


def money(value: Decimal | float | int) -> Decimal:
    """金额保留 2 位（四舍五入到分）"""
    return D(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def yuan(value: Decimal | float | int) -> Decimal:
    """金额取整到元（公积金部分地区按元取整）"""
    return D(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


@dataclass
class TaxBracket:
    upper: Decimal | None
    rate: Decimal
    quick: Decimal


@dataclass
class Rates:
    """五险一金个人缴纳比例（%）及医疗固定额"""
    pension: Decimal = Decimal("8")
    medical: Decimal = Decimal("2")
    medical_fixed: Decimal = Decimal("0")
    unemployment: Decimal = Decimal("0.5")
    housing: Decimal = Decimal("7")
    supplemental_housing: Decimal = Decimal("0")

    @classmethod
    def from_dict(cls, data: dict | None, fallback: "Rates | None" = None) -> "Rates":
        base = fallback or cls()
        data = data or {}
        return cls(
            pension=D(data.get("pension", base.pension)),
            medical=D(data.get("medical", base.medical)),
            medical_fixed=D(data.get("medical_fixed", base.medical_fixed)),
            unemployment=D(data.get("unemployment", base.unemployment)),
            housing=D(data.get("housing", base.housing)),
            supplemental_housing=D(data.get("supplemental_housing", base.supplemental_housing)),
        )


@dataclass
class PeriodPolicy:
    """半年度区间政策：社保 / 公积金基数上下限"""
    months: tuple[int, int]
    pension: tuple[Decimal, Decimal]
    medical: tuple[Decimal, Decimal]
    unemployment: tuple[Decimal, Decimal]
    housing: tuple[Decimal, Decimal]
    provisional: bool = False


@dataclass
class CityPolicy:
    code: str
    name: str
    province: str
    rent_deduction: Decimal
    defaults: Rates
    housing_rate: tuple[Decimal, Decimal]
    supplemental_housing_rate: tuple[Decimal, Decimal]
    years: dict[int, list[PeriodPolicy]] = field(default_factory=dict)

    def period(self, year: int, month: int) -> PeriodPolicy:
        periods = self.years.get(year)
        if not periods:
            raise PolicyError(f"{self.name} 暂不支持 {year} 年度政策（已配置年度：{sorted(self.years)}）")
        for p in periods:
            if p.months[0] <= month <= p.months[1]:
                return p
        return periods[-1]

    def supported_years(self) -> list[int]:
        return sorted(self.years)


class PolicyError(Exception):
    pass


class Policy:
    """统一政策入口"""

    def __init__(self, national: dict, cities: dict):
        self.national_raw = national
        self.monthly_basic_deduction = D(national["basic_deduction"]["monthly"])
        self.annual_basic_deduction = D(national["basic_deduction"]["annual"])

        self.tax_brackets = [
            TaxBracket(
                upper=None if b["upper"] is None else D(b["upper"]),
                rate=D(b["rate"]),
                quick=D(b["quick_deduction"]),
            )
            for b in national["tax_brackets"]
        ]
        self.bonus_brackets = [
            TaxBracket(
                upper=None if b["upper"] is None else D(b["upper"]),
                rate=D(b["rate"]),
                quick=D(b["quick_deduction"]),
            )
            for b in national["annual_bonus_brackets"]
        ]
        self.bonus_policy = national.get("annual_bonus_policy", {})
        self.special_deductions: dict[str, dict[str, Any]] = national.get("special_deductions", {})
        self.medical_deduction = national.get("medical_deduction", {})
        self.validation = national.get("validation", {})

        self.cities: dict[str, CityPolicy] = {}
        for code, raw in cities["cities"].items():
            years: dict[int, list[PeriodPolicy]] = {}
            for year, ydata in raw["years"].items():
                years[int(year)] = [
                    PeriodPolicy(
                        months=tuple(p["months"]),
                        pension=tuple(D(x) for x in p["pension"]),
                        medical=tuple(D(x) for x in p["medical"]),
                        unemployment=tuple(D(x) for x in p["unemployment"]),
                        housing=tuple(D(x) for x in p["housing"]),
                        provisional=bool(p.get("provisional", False)),
                    )
                    for p in ydata["periods"]
                ]
            defaults = Rates.from_dict(raw.get("defaults"))
            self.cities[code] = CityPolicy(
                code=code,
                name=raw["name"],
                province=raw.get("province", ""),
                rent_deduction=D(raw.get("rent_deduction", 1500)),
                defaults=defaults,
                housing_rate=tuple(D(x) for x in raw.get("housing_rate", [5, 12])),
                supplemental_housing_rate=tuple(D(x) for x in raw.get("supplemental_housing_rate", [0, 12])),
                years=years,
            )

    # ---------------- 加载 ----------------
    @classmethod
    def load(cls, policy_dir: Path | str | None = None) -> "Policy":
        base = Path(policy_dir) if policy_dir else POLICY_DIR
        with open(base / "national.yaml", "r", encoding="utf-8") as f:
            national = yaml.safe_load(f)
        with open(base / "cities.yaml", "r", encoding="utf-8") as f:
            cities = yaml.safe_load(f)
        return cls(national, cities)

    # ---------------- 城市解析 ----------------
    def resolve_city(self, token: str | None, default: str | None = None) -> CityPolicy:
        """支持城市代码(shanghai)、中文名(上海)、'上海市'、'上海社保' 等多种写法"""
        if not token or not str(token).strip():
            token = default
        if not token:
            raise PolicyError("未指定参保城市，且无默认城市可用")
        raw = str(token).strip().lower()
        for code, city in self.cities.items():
            if raw == code or raw == city.name or raw in city.name:
                return city
        for code, city in self.cities.items():
            if city.name and city.name in str(token):
                return city
        raise PolicyError(f"无法识别的参保城市：{token}（已支持：{'、'.join(c.name for c in self.cities.values())}）")

    def city_options(self) -> list[dict]:
        return [
            {
                "code": c.code,
                "name": c.name,
                "province": c.province,
                "years": c.supported_years(),
            }
            for c in self.cities.values()
        ]

    # ---------------- 税额计算 ----------------
    def tax_of(self, taxable: Decimal, brackets: list[TaxBracket] | None = None) -> tuple[Decimal, Decimal]:
        """返回 (税额, 适用税率)"""
        brackets = brackets or self.tax_brackets
        if taxable <= 0:
            return Decimal("0"), Decimal("0")
        for b in brackets:
            if b.upper is None or taxable <= b.upper:
                return money(taxable * b.rate - b.quick), b.rate
        last = brackets[-1]
        return money(taxable * last.rate - last.quick), last.rate

    def annual_bonus_tax(self, bonus: Decimal) -> tuple[Decimal, Decimal]:
        """全年一次性奖金单独计税：以 bonus/12 查月度换算表"""
        if bonus <= 0:
            return Decimal("0"), Decimal("0")
        monthly = (bonus / Decimal("12")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        for b in self.bonus_brackets:
            if b.upper is None or monthly <= b.upper:
                return money(bonus * b.rate - b.quick), b.rate
        last = self.bonus_brackets[-1]
        return money(bonus * last.rate - last.quick), last.rate

    # ---------------- 专项附加扣除标准 ----------------
    def deduction_cap(self, key: str, city: CityPolicy | None = None) -> Decimal | None:
        item = self.special_deductions.get(key)
        if not item:
            return None
        if item.get("by_city") and city is not None:
            return city.rent_deduction
        cap = item.get("monthly_cap")
        return None if cap is None else D(cap)

    def deduction_label(self, key: str) -> str:
        item = self.special_deductions.get(key, {})
        return item.get("label", key)
