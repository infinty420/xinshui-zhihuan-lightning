"""
标准化工资表 Schema 与列名别名词典
==================================
各类客户的工资表表头千差万别（"基本工资"/"基础工资"/"base"/"岗位薪"…），
本模块用别名词典把任意表头映射到统一字段，是「适配多格式工资表」的核心。

字段分五组：
  identity   身份与期间
  income     收入项
  insurance  社保公积金（基数 / 比例 / 已缴金额）
  deduction  扣除项
  baseline   客户端既有结果（用于闪电校验的差异比对）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 字段定义
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Field:
    key: str
    label: str
    group: str
    aliases: tuple[str, ...] = ()
    required: bool = False
    desc: str = ""


FIELDS: tuple[Field, ...] = (
    # ---------------- 身份与期间 ----------------
    Field("employee_id", "员工编号", "identity",
          ("工号", "员工工号", "员工编号", "员工号", "编号", "职工号", "职工编号", "人员编号", "人员编码",
           "empno", "emp_no", "employeeid", "employeeid", "staffid", "staff_id", "id", "no", "code"),
          required=True, desc="唯一标识一名员工的编号"),
    Field("name", "姓名", "identity",
          ("姓名", "员工姓名", "职工姓名", "人员姓名", "名字", "name", "employeename", "staffname", "fullname"),
          required=True, desc="员工姓名"),
    Field("id_no", "证件号码", "identity",
          ("身份证号", "身份证号码", "身份证", "证件号", "证件号码", "身份证号吗", "证件编号",
           "idcard", "idcardno", "idno", "id_no", "identityno", "identification"),
          desc="居民身份证号，用于年度汇算与申报"),
    Field("department", "部门", "identity",
          ("部门", "所属部门", "部门名称", "单位", "成本中心", "department", "dept", "org"),
          desc="所属部门 / 成本中心"),
    Field("city", "参保城市", "identity",
          ("城市", "参保城市", "社保城市", "缴纳城市", "参保地", "社保参保地", "工作地", "工作城市",
           "city", "location", "workcity", "socialcity"),
          desc="社保公积金参保城市，决定基数上下限与租金扣除标准"),
    Field("year", "年度", "identity",
          ("年度", "年份", "所属年度", "年", "year", "yyyy"),
          desc="核算年度"),
    Field("month", "月份", "identity",
          ("月份", "月", "所属月份", "期间", "所属期", "工资月份", "计薪月", "月次", "month", "period", "mm"),
          desc="核算月份 1-12"),
    Field("entry_month", "入职月份", "identity",
          ("入职月份", "入职月", "入司月", "起始月", "起薪月", "entrymonth", "hiremonth", "startmonth"),
          desc="年内入职月份，决定累计减除费用月数"),
    Field("exit_month", "离职月份", "identity",
          ("离职月份", "离职月", "离司月", "结束月", "止薪月", "exitmonth", "leavemonth", "endmonth"),
          desc="年内离职月份"),

    # ---------------- 收入项 ----------------
    Field("basic_salary", "基本工资", "income",
          ("基本工资", "基础工资", "底薪", "标准工资", "岗位工资", "本薪", "基本薪", "基础薪",
           "basicsalary", "base", "basepay", "basesalary")),
    Field("position_salary", "岗位工资", "income",
          ("岗位工资", "职位工资", "职务工资", "职级工资", "岗位薪", "positionsalary", "postsalary")),
    Field("performance", "绩效工资", "income",
          ("绩效工资", "绩效", "绩效奖金", "绩效奖", "考核工资", "奖金绩效", "浮动工资", "业绩工资",
           "performance", "merit", "kpi")),
    Field("allowance", "津贴补贴", "income",
          ("津贴", "补贴", "津贴补贴", "各类补贴", "补助", "餐补", "交通补贴", "通讯补贴", "岗位津贴",
           "allowance", "subsidy", "benefit")),
    Field("overtime", "加班工资", "income",
          ("加班费", "加班工资", "加班", "加班薪资", "ot", "overtime", "overtimepay")),
    Field("bonus", "奖金", "income",
          ("奖金", "月度奖金", "月奖", "季度奖", "奖励", "其他奖金", "bonus", "award")),
    Field("other_income", "其他收入", "income",
          ("其他收入", "其他应发", "补发", "其他工资", "其他", "杂项", "otherincome", "other", "misc")),
    Field("annual_bonus", "全年一次性奖金", "income",
          ("年终奖", "全年一次性奖金", "年终奖金", "年度奖金", "十三薪", "年终绩效", "年奖",
           "annualbonus", "yearbonus", "yearendbonus")),
    Field("gross", "应发工资合计", "income",
          ("应发工资", "应发合计", "应发", "应发总额", "应发金额", "税前工资", "税前合计", "工资总额", "本月应发",
           "gross", "grosspay", "grosssalary", "pretax", "totalsalary")),

    # ---------------- 社保公积金：基数 ----------------
    Field("social_base", "社保申报基数", "insurance",
          ("社保基数", "社保申报基数", "社保缴费基数", "社保缴纳基数", "社会保险基数", "缴费基数", "社保工资",
           "socialbase", "insurancebase", "sibase")),
    Field("housing_base", "公积金申报基数", "insurance",
          ("公积金基数", "公积金申报基数", "公积金缴费基数", "公积金缴纳基数", "住房公积金基数",
           "housingbase", "fundbase", "hfbbase")),

    # ---------------- 社保公积金：比例（覆盖城市默认） ----------------
    Field("pension_rate", "养老个人比例%", "insurance", ("养老比例", "养老个人比例", "养老保险比例", "pensionrate")),
    Field("medical_rate", "医疗个人比例%", "insurance", ("医疗比例", "医疗个人比例", "医疗保险比例", "medicalrate")),
    Field("unemployment_rate", "失业个人比例%", "insurance", ("失业比例", "失业个人比例", "失业保险比例", "unemploymentrate")),
    Field("housing_rate", "公积金个人比例%", "insurance",
          ("公积金比例", "公积金个人比例", "住房公积金比例", "housingrate", "fundrate")),
    Field("supplemental_housing_rate", "补充公积金个人比例%", "insurance",
          ("补充公积金比例", "补充住房公积金比例", "补充公积金个人比例", "supplementalhousingrate")),

    # ---------------- 社保公积金：已缴金额（客户提供时直接使用） ----------------
    Field("pension", "养老个人缴纳", "insurance",
          ("养老", "养老保险", "养老金个人", "个人养老", "养老保险费", "养老个人", "pension", "endowment")),
    Field("medical", "医疗个人缴纳", "insurance",
          ("医疗", "医疗保险", "医保", "个人医疗", "医疗保险费", "医疗个人", "medical", "healthinsurance")),
    Field("unemployment", "失业个人缴纳", "insurance",
          ("失业", "失业保险", "个人失业", "失业保险费", "失业个人", "unemployment")),
    Field("housing", "公积金个人缴纳", "insurance",
          ("公积金", "住房公积金", "个人公积金", "住房公积金个人", "住房公积", "housing", "housingfund", "gjj")),
    Field("supplemental_housing", "补充公积金个人缴纳", "insurance",
          ("补充公积金", "补充住房公积金", "住房补充公积金", "supplementalhousing")),
    Field("contributions", "五险一金个人合计", "insurance",
          ("五险一金", "社保公积金", "五险一金合计", "个人社保公积金合计", "社保合计", "个人缴纳合计",
           "五险一金个人", "社保公积金个人合计", "contributions", "socialinsurance")),

    # ---------------- 专项附加扣除 ----------------
    Field("children", "子女教育", "deduction",
          ("子女教育", "子女教育支出", "子女教育扣除", "子女", "children", "childeducation")),
    Field("infant", "3岁以下婴幼儿照护", "deduction",
          ("婴幼儿照护", "三岁以下婴幼儿照护", "3岁以下婴幼儿照护", "婴幼儿", "托育", "infant", "childcare")),
    Field("education", "继续教育", "deduction",
          ("继续教育", "继续教育扣除", "学历继续教育", "education", "continuededucation")),
    Field("mortgage", "住房贷款利息", "deduction",
          ("住房贷款利息", "房贷利息", "房贷", "住房贷款", "mortgage", "housingloan")),
    Field("rent", "住房租金", "deduction",
          ("住房租金", "房租", "租金", "租房", "住房租金扣除", "rent", "housingrent")),
    Field("elderly", "赡养老人", "deduction",
          ("赡养老人", "赡养老人扣除", "赡养", "elderly", "elderlycare")),
    Field("special_deduction_total", "专项附加扣除合计", "deduction",
          ("专项附加扣除", "专项附加扣除合计", "附加扣除", "专项附加", "扣除合计",
           "specialdeduction", "additionaldeduction")),
    Field("other_deduction", "其他扣除", "deduction",
          ("其他扣除", "其他税前扣除", "依法确定的其他扣除", "其他专项扣除", "otherdeduction")),
    Field("annual_medical", "大病医疗", "deduction",
          ("大病医疗", "大病医疗扣除", "大病", "annualmedical", "medicaldeduction")),

    # ---------------- 跨期累计接续（中途接手项目 / 历史系统迁移时使用） ----------------
    Field("prev_income", "累计已申报收入额", "carryover",
          ("累计收入", "累计收入额", "上期累计收入", "累计应发", "previncome", "cumulativeincome")),
    Field("prev_contributions", "累计已申报专项扣除", "carryover",
          ("累计专项扣除", "累计五险一金", "上期累计社保", "prevcontributions")),
    Field("prev_special", "累计已申报专项附加扣除", "carryover",
          ("累计专项附加扣除", "上期累计专项附加", "prevspecial")),
    Field("prev_other", "累计已申报其他扣除", "carryover",
          ("累计其他扣除", "上期累计其他扣除", "prevother")),
    Field("prev_withheld", "累计已预扣税额", "carryover",
          ("累计已预扣税额", "累计已缴税额", "上期累计个税", "已预扣税额", "累计个税",
           "prevwithheld", "withheld", "cumulativetax")),

    # ---------------- 客户端既有结果（闪电校验比对基准） ----------------
    Field("client_tax", "客户端个税", "baseline",
          ("个人所得税", "个税", "代扣个税", "应扣个税", "工资薪金个税", "已扣个税", "客户个税", "系统个税",
           "tax", "iit", "incometax", "personaltax")),
    Field("client_net", "客户端实发", "baseline",
          ("实发工资", "实发", "实发合计", "实发金额", "税后工资", "到手工资", "净额", "客户实发", "系统实发",
           "net", "netpay", "takehome", "netsalary")),
    Field("client_contributions", "客户端五险一金", "baseline",
          ("客户五险一金", "系统五险一金", "原五险一金", "clientcontributions")),
)

FIELD_MAP: dict[str, Field] = {f.key: f for f in FIELDS}
INCOME_KEYS: tuple[str, ...] = (
    "basic_salary", "position_salary", "performance", "allowance",
    "overtime", "bonus", "other_income",
)
DEDUCTION_KEYS: tuple[str, ...] = ("children", "infant", "education", "mortgage", "rent", "elderly")

# ---------------------------------------------------------------------------
# 表头归一化 & 匹配
# ---------------------------------------------------------------------------
_NOISE = re.compile(r"[\s\*\(\)（）\[\]【】:：、,，。.\-/\\_'\"`#￥元%｜|]+")


def normalize(text) -> str:
    """表头归一化：去空白与噪声字符、全角转半角、小写"""
    if text is None:
        return ""
    s = str(text).strip()
    # 全角转半角
    s = s.translate(str.maketrans(
        "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ０１２３４５６７８９",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789",
    ))
    s = _NOISE.sub("", s)
    return s.lower()


# 预构建索引：归一化别名 -> 标准字段
_ALIAS_INDEX: dict[str, str] = {}
for _f in FIELDS:
    _ALIAS_INDEX[normalize(_f.label)] = _f.key
    for _a in _f.aliases:
        _ALIAS_INDEX[normalize(_a)] = _f.key

# 长别名优先，避免 "公积金" 抢走 "公积金基数"
_ORDERED_ALIASES: list[tuple[str, str]] = sorted(
    ((normalize(a), f.key) for f in FIELDS for a in (*f.aliases, f.label)),
    key=lambda x: len(x[0]),
    reverse=True,
)


def match_field(header: str) -> str | None:
    """把任意表头匹配到标准字段 key；无法匹配返回 None"""
    if not header:
        return None
    norm = normalize(header)
    if not norm:
        return None
    # 1) 精确匹配
    if norm in _ALIAS_INDEX:
        return _ALIAS_INDEX[norm]
    # 2) 别名作为子串出现在表头中（长别名优先）
    for alias, key in _ORDERED_ALIASES:
        if not alias:
            continue
        if alias == norm or (len(alias) >= 2 and alias in norm):
            return key
    return None


@dataclass
class MappingResult:
    """列映射结果"""
    mapping: dict[str, str] = field(default_factory=dict)   # 标准字段 -> 原始表头
    unmapped: list[str] = field(default_factory=list)       # 未识别的原始表头
    missing: list[str] = field(default_factory=list)        # 缺失的必填字段

    @property
    def ok(self) -> bool:
        return not self.missing


def build_mapping(headers: list[str]) -> MappingResult:
    """对一批表头做智能映射"""
    result = MappingResult()
    used: set[str] = set()
    for h in headers:
        key = match_field(str(h))
        if key and key not in used:
            result.mapping[key] = str(h)
            used.add(key)
        else:
            result.unmapped.append(str(h))
    result.missing = [f.key for f in FIELDS if f.required and f.key not in result.mapping]
    return result
