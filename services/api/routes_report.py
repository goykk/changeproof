"""证据报告读取端点。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.store import SqliteStore
from services.api.deps import ApiError, get_store
from services.api.schemas import ReportOut

router = APIRouter(prefix="/api", tags=["report"])


@router.get("/changesets/{changeset_id}/report", response_model=ReportOut)
def get_report(changeset_id: str, store: SqliteStore = Depends(get_store)) -> ReportOut:
    if store.changesets.get_changeset(changeset_id) is None:
        raise ApiError(404, "not_found", f"变更集不存在: {changeset_id}")
    payload = store.reports.get_report(changeset_id)
    if not payload:
        raise ApiError(404, "report_not_ready", f"报告尚未生成: {changeset_id}")
    return ReportOut.model_validate(payload)