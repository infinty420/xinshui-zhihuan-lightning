"""
薪税智算·闪电校验 —— Web 服务

启动：
    python cli.py serve --port 8765
或：
    python -m uvicorn web.app:app --port 8765
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from core.adapter import list_sheets, load_sheet
from core.pipeline import RunOptions, run_pipeline
from core.policy import Policy
from core.schema import FIELD_MAP, build_mapping

ROOT = Path(__file__).resolve().parent.parent
UPLOAD_DIR = ROOT / "uploads"
OUTPUT_DIR = ROOT / "outputs"
STATIC_DIR = Path(__file__).resolve().parent / "static"
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ALLOWED_EXT = {".xlsx", ".xlsm", ".xls", ".csv", ".txt"}

app = FastAPI(title="薪税智算·闪电校验", version="1.0.0")
policy = Policy.load()

# job_id -> 报表路径
JOBS: dict[str, Path] = {}


def _save_upload(file: UploadFile) -> Path:
    ext = Path(file.filename or "upload").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"不支持的文件格式 {ext}，请上传 xlsx / xls / csv")
    token = uuid.uuid4().hex[:12]
    dest = UPLOAD_DIR / f"{token}_{file.filename}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return dest


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/cities")
async def cities():
    return {"cities": policy.city_options(),
            "bonus_methods": [{"value": "auto", "label": "自动择优（推荐）"},
                              {"value": "separate", "label": "单独计税"},
                              {"value": "merge", "label": "并入综合所得"}],
            "fields": [{"key": f.key, "label": f.label, "group": f.group, "required": f.required,
                        "desc": f.desc} for f in FIELD_MAP.values()]}


@app.post("/api/inspect")
async def inspect(file: UploadFile = File(...), sheet: str | None = Form(None)):
    """上传并解析工资表，返回列映射结果供人工确认"""
    path = _save_upload(file)
    try:
        sheets = list_sheets(path)
        data = load_sheet(path, sheet)
        mapping = build_mapping(data.headers)
        return {
            "file_id": path.name,
            "file_name": file.filename,
            "sheets": sheets,
            "sheet": data.name,
            "header_row": data.header_row_index + 1,
            "row_count": len(data.rows),
            "headers": data.headers,
            "mapping": mapping.mapping,
            "unmapped": mapping.unmapped,
            "missing": [FIELD_MAP[m].label for m in mapping.missing],
            "preview": [{h: row.get(h) for h in data.headers} for row in data.rows[:5]],
        }
    except Exception as exc:                                   # noqa: BLE001
        raise HTTPException(400, f"解析失败：{exc}") from exc


@app.post("/api/run")
async def run(
    file_id: str = Form(...),
    sheet: str | None = Form(None),
    city: str | None = Form(None),
    year: int | None = Form(None),
    month: int | None = Form(None),
    bonus: str = Form("auto"),
    declaration: str = Form("monthly"),
    client: str = Form(""),
    mapping_json: str | None = Form(None),
):
    path = UPLOAD_DIR / file_id
    if not path.exists():
        raise HTTPException(404, "上传文件不存在，请重新上传")

    mapping_override = None
    if mapping_json:
        try:
            mapping_override = json.loads(mapping_json)
        except json.JSONDecodeError:
            raise HTTPException(400, "列映射参数格式错误") from None

    out_name = f"{datetime.now():%Y%m%d_%H%M%S}_{(client or '算薪报表').replace('/', '_')}.xlsx"
    options = RunOptions(
        input_path=str(path),
        out_path=str(OUTPUT_DIR / out_name),
        sheet=sheet,
        city=city or None,
        year=year,
        month=month,
        bonus_method=bonus,
        declaration_method=declaration,
        client=client,
        mapping_override=mapping_override,
    )
    try:
        outcome = run_pipeline(options, policy)
    except Exception as exc:                                   # noqa: BLE001
        raise HTTPException(400, f"核算失败：{exc}") from exc

    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = Path(outcome.report_path)

    return {
        "job_id": job_id,
        "download_url": f"/api/download/{job_id}",
        "file_name": Path(outcome.report_path).name,
        "sheet": outcome.sheet_name,
        "layout": {"long": "长表", "wide": "宽表", "single": "单月表"}.get(outcome.layout, outcome.layout),
        "notes": outcome.notes,
        "summary": outcome.summary(),
        "issues": [i.as_row() for i in outcome.validation.issues[:200]],
        "issue_total": len(outcome.validation.issues),
        "yearly": [yr.as_row() for yr in outcome.batch.employees[:100]],
        "skipped": outcome.batch.skipped[:10],
        "generated_at": outcome.generated_at,
    }


@app.get("/api/download/{job_id}")
async def download(job_id: str):
    path = JOBS.get(job_id)
    if not path or not path.exists():
        raise HTTPException(404, "报表不存在或已过期，请重新核算")
    return FileResponse(path, filename=path.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.exception_handler(Exception)
async def _unhandled(request, exc):                            # noqa: ANN001
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})
