"""
薪税智算·闪电校验 —— 命令行入口

用法示例
--------
# 1. 先看看工资表被识别成了什么样（不计算，确认列映射）
python cli.py inspect 客户A工资表.xlsx

# 2. 批量核算并输出标准化算薪报表
python cli.py run 客户A工资表.xlsx --city 上海 --year 2026 -o 客户A_算薪报表.xlsx

# 3. 单月工资表（表里没有月份列）
python cli.py run 客户B_2026年3月工资表.csv --city 深圳 --year 2026 --month 3 -o out.xlsx

# 4. 启动 Web 界面
python cli.py serve --port 8765
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.adapter import list_sheets, load_sheet
from core.pipeline import RunOptions, run_pipeline
from core.policy import Policy
from core.schema import FIELD_MAP, build_mapping


def _print_mapping(path: str, sheet: str | None) -> None:
    data = load_sheet(path, sheet)
    mapping = build_mapping(data.headers)
    print(f"工作表：{data.name}    表头行：第 {data.header_row_index + 1} 行    数据行数：{len(data.rows)}")
    print("\n已识别列映射：")
    for key, header in mapping.mapping.items():
        field = FIELD_MAP[key]
        print(f"  {header:<24} -> {field.label} ({key})")
    if mapping.unmapped:
        print("\n未识别列（如需参与计算请手工指定映射）：")
        for h in mapping.unmapped:
            print(f"  {h}")
    if mapping.missing:
        print("\n缺失必填字段：" + "、".join(FIELD_MAP[m].label for m in mapping.missing))
    print("\n前 3 行预览：")
    for row in data.rows[:3]:
        print("  " + json.dumps({k: v for k, v in row.items()}, ensure_ascii=False, default=str)[:400])


def cmd_inspect(args: argparse.Namespace) -> int:
    if args.sheets:
        print("可用工作表：" + "、".join(list_sheets(args.input)))
        return 0
    _print_mapping(args.input, args.sheet)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    out = args.out or str(Path(args.input).with_name(Path(args.input).stem + "_算薪报表.xlsx"))
    options = RunOptions(
        input_path=args.input,
        out_path=out,
        sheet=args.sheet,
        city=args.city,
        year=args.year,
        month=args.month,
        bonus_method=args.bonus,
        declaration_method=args.declaration,
        client=args.client or "",
        defaults=json.loads(args.defaults) if args.defaults else {},
    )
    outcome = run_pipeline(options)

    print(f"源文件      ：{args.input}（工作表 {outcome.sheet_name}，"
          f"{ {'long': '长表', 'wide': '宽表', 'single': '单月表'}.get(outcome.layout, outcome.layout)} ）")
    for note in outcome.notes:
        print(f"  · {note}")
    print(f"核算范围    ：{outcome.batch.year} 年 {outcome.batch.city_name}    "
          f"员工 {len(outcome.batch.employees)} 人   记录 {len(outcome.records)} 条")
    if outcome.batch.skipped:
        print(f"跳过记录    ：{len(outcome.batch.skipped)} 条（例：{outcome.batch.skipped[0].get('reason')}）")

    s = outcome.validation.summary()
    print("\n闪电校验    ：" + "    ".join(f"{k} {v}" for k, v in s.items()))

    print("\n异常清单（前 20 条）：")
    for issue in outcome.validation.issues[:20]:
        loc = f"{issue.employee_id} {issue.name} {issue.month or ''}月".strip()
        print(f"  [{ {'error': '错误', 'warning': '警告', 'info': '提示'}[issue.level]}] "
              f"{issue.code} {loc} - {issue.message}")
    if len(outcome.validation.issues) > 20:
        print(f"  … 其余 {len(outcome.validation.issues) - 20} 条见报表「异常校验清单」")

    print(f"\n报表已生成  ：{outcome.report_path}")
    return 0


def cmd_group(args: argparse.Namespace) -> int:
    """集团级批量：一个目录下的多家主体工资表一次跑完"""
    from core.pipeline import EntityPlan, RunOptions, run_group

    src = Path(args.dir)
    files = sorted(p for p in src.iterdir()
                   if p.suffix.lower() in (".xlsx", ".xlsm", ".xls", ".csv", ".txt")
                   and not p.name.startswith("~$"))
    if not files:
        print(f"目录 {src} 下没有可处理的工资表", file=sys.stderr)
        return 1

    default_city = json.loads(args.city_map) if args.city_map else {}
    plans = []
    for p in files:
        entity = p.stem
        # 城市解析优先级：映射表 > 文件名中出现城市名 > 命令行默认
        city = None
        for name, c in default_city.items():
            if name in p.name:
                city = c
                break
        if city is None:
            for c in ("上海", "北京", "广州", "深圳", "杭州", "南京", "合肥", "芜湖"):
                if c in p.name:
                    city = c
                    break
        plans.append(EntityPlan(entity=entity, input_path=str(p), city=city or args.default_city,
                                month=args.month))

    base = RunOptions(input_path="", year=args.year, bonus_method=args.bonus,
                      declaration_method=args.declaration)
    outcome = run_group(plans, args.out, base)

    print(f"集团批量核算：共 {len(files)} 家主体，输出目录 {args.out}\n")
    for entity, o in outcome.entity_outcomes:
        s = o.validation.summary()
        print(f"  [{entity}]  {o.batch.year}年 {o.batch.city_name}  "
              f"员工 {len(o.batch.employees)} 人  记录 {len(o.records)} 条  "
              f"错误 {s['错误']}  警告 {s['警告']}  不一致 {s['比对不一致']}")
    for entity, msg in outcome.errors:
        print(f"  [{entity}]  失败：{msg}")
    print("\n汇总：" + "    ".join(f"{k} {v}" for k, v in outcome.combined_summary().items()))
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run("web.app:app", host=args.host, port=args.port, reload=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="薪税智算",
        description="薪税智算·闪电校验 —— 多格式工资表标准化算薪与个税批量核算",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("inspect", help="查看工资表解析结果与列映射")
    pi.add_argument("input")
    pi.add_argument("--sheet", help="指定工作表名")
    pi.add_argument("--sheets", action="store_true", help="只列出所有工作表名")
    pi.set_defaults(func=cmd_inspect)

    pr = sub.add_parser("run", help="批量核算并输出标准化算薪报表")
    pr.add_argument("input")
    pr.add_argument("-o", "--out", help="输出报表路径")
    pr.add_argument("--sheet", help="指定工作表名")
    pr.add_argument("--city", help="参保城市（表内无城市列时使用）")
    pr.add_argument("--year", type=int, help="核算年度")
    pr.add_argument("--month", type=int, help="月份（单月表必填）")
    pr.add_argument("--bonus", choices=["auto", "separate", "merge"], default="auto",
                    help="年终奖计税方式：auto 自动择优 / separate 单独计税 / merge 并入综合所得")
    pr.add_argument("--declaration", choices=["monthly", "annual"], default="monthly",
                    help="专项附加扣除申报方式")
    pr.add_argument("--client", help="客户/项目名称，写入报表封面")
    pr.add_argument("--defaults", help='JSON 形式的默认值，如 \'{"other_deduction": 0}\'')
    pr.set_defaults(func=cmd_run)

    ps = sub.add_parser("group", help="集团级批量：一个目录下多家主体工资表一次跑完")
    ps.add_argument("dir", help="存放各主体工资表的目录")
    ps.add_argument("-o", "--out", required=True, help="报表输出目录")
    ps.add_argument("--year", type=int, help="核算年度")
    ps.add_argument("--month", type=int, help="月份（单月表适用）")
    ps.add_argument("--default-city", help="文件名中识别不出城市时的兜底城市")
    ps.add_argument("--city-map", help='文件名关键词到城市的映射，如 \'{"上海所":"上海","北京所":"北京"}\'')
    ps.add_argument("--bonus", choices=["auto", "separate", "merge"], default="auto")
    ps.add_argument("--declaration", choices=["monthly", "annual"], default="monthly")
    ps.set_defaults(func=cmd_group)

    psrv = sub.add_parser("serve", help="启动 Web 界面")
    psrv.add_argument("--host", default="127.0.0.1")
    psrv.add_argument("--port", type=int, default=8765)
    psrv.set_defaults(func=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:                                  # noqa: BLE001
        print(f"执行失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
