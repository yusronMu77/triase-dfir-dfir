"""Endpoint internal dfir (`/internal/v1`, note app-dfir 3.1). Hanya `svc:api` yang boleh memanggil.

Peran pengguna sudah diperiksa api (izin `cards:deep_dive` dan `cases:read`); dfir mencatat `act.sub` sebagai
peminta kasus. Kasus dibangun di latar setelah balasan 202.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any, Literal

from astria_common.auth import ServicePrincipal, require_service
from astria_common.db import Database
from astria_common.errors import AstriaError
from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncEngine

from astria_dfir import cases
from astria_dfir.custody import rantai_utuh
from astria_dfir.timeline import iso_utc

router = APIRouter(prefix="/internal/v1", tags=["dfir"])

Caller = Annotated[ServicePrincipal, Depends(require_service("svc:dfir", {"svc:api"}))]
CaseId = Annotated[str, Path(pattern=cases.CASE_ID_PATTERN)]
# Laporan berisi teks log yang sudah di-escape; CSP tetap menolak skrip dan sumber luar sebagai lapis kedua.
REPORT_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
}


class CaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    card_id: str = Field(pattern=r"^IC-[0-9]{8}-[0-9]{3,}$")


def _engine(request: Request) -> AsyncEngine:
    db: Database | None = request.app.state.components.get("postgres")
    if db is None or not db.is_ready:
        raise AstriaError("DFIR_TIDAK_SIAP", "basis data dfir belum siap", 503)
    return db.engine


def _ringkas(row: Any) -> dict[str, Any]:
    case_id = row["case_id"]
    return {
        "case_id": case_id,
        "status": row["status"],
        "card_id": row["card_id"],
        "card_version": row["card_version"],
        "links": {
            "self": f"/internal/v1/cases/{case_id}",
            "timeline": f"/internal/v1/cases/{case_id}/timeline",
            "custody": f"/internal/v1/cases/{case_id}/custody",
            "report": f"/internal/v1/cases/{case_id}/report?format=html",
        },
    }


async def _kasus(conn: Any, case_id: str) -> Any:
    row = await cases.kasus(conn, case_id)
    if row is None:
        raise AstriaError("KASUS_TIDAK_DITEMUKAN", "kasus tidak ditemukan", 404)
    return row


@router.post("/cases", status_code=202)
async def create_case(
    body: CaseRequest, request: Request, background: BackgroundTasks, caller: Caller
) -> dict[str, Any]:
    engine = _engine(request)
    requested_by = str((caller.act or {}).get("sub") or caller.subject)
    async with engine.begin() as conn:
        row, baru = await cases.buat_kasus(conn, body.card_id, requested_by)
    if baru:
        background.add_task(cases.bangun_kasus, engine, request.app.state.evidence, row["case_id"])
    return _ringkas(row)


@router.get("/cases/{case_id}")
async def get_case(case_id: CaseId, request: Request, _caller: Caller) -> dict[str, Any]:
    async with _engine(request).connect() as conn:
        row = await _kasus(conn, case_id)
        laporan = await cases.laporan_terbaru(conn, case_id, html=False)
    return _ringkas(row) | {
        "requested_by": row["requested_by"],
        "window": {"start": iso_utc(row["window_start"]), "end": iso_utc(row["window_end"])},
        "keterbatasan": row["keterbatasan"],
        "created_at": iso_utc(row["created_at"]),
        "updated_at": iso_utc(row["updated_at"]),
        "report": {"version": laporan["version"], "html_sha256": laporan["html_sha256"]} if laporan else None,
    }


@router.get("/cases/{case_id}/timeline")
async def get_timeline(
    case_id: CaseId,
    request: Request,
    _caller: Caller,
    cursor: Annotated[str | None, Query(pattern=r"^TL-[0-9]{8}-[0-9]{3,}-[0-9]{5}$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    async with _engine(request).connect() as conn:
        row = await _kasus(conn, case_id)
        items = await cases.linimasa_kasus(conn, case_id, cursor, limit + 1)
    halaman = items[:limit]
    return {
        "case_id": case_id,
        "status": row["status"],
        "items": [dict(i) | {"ts_utc": iso_utc(i["ts_utc"])} for i in halaman],
        "next_cursor": halaman[-1]["item_id"] if len(items) > limit else None,
    }


@router.get("/cases/{case_id}/custody")
async def get_custody(case_id: CaseId, request: Request, _caller: Caller) -> dict[str, Any]:
    async with _engine(request).connect() as conn:
        await _kasus(conn, case_id)
        entri = await cases.baca_custody(conn, case_id)
    return {"case_id": case_id, "entries": [asdict(e) for e in entri], "chain_ok": rantai_utuh(entri, case_id)}


@router.get("/cases/{case_id}/report", response_class=HTMLResponse)
async def get_report(
    case_id: CaseId,
    request: Request,
    _caller: Caller,
    format: Literal["html"] = "html",
) -> HTMLResponse:
    async with _engine(request).connect() as conn:
        row = await _kasus(conn, case_id)
        laporan = await cases.laporan_terbaru(conn, case_id)
    if laporan is None:
        raise AstriaError("LAPORAN_BELUM_ADA", f"laporan belum ada (status {row['status']})", 409)
    headers = REPORT_HEADERS | {"X-Report-SHA256": laporan["html_sha256"]}
    return HTMLResponse(laporan["html"], headers=headers)
