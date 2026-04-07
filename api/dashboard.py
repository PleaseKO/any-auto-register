import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from sqlmodel import Session, select, func

from core.config_store import config_store
from core.db import AccountModel, OutlookAccountModel, TaskLog, engine
from api.tasks import _task_store

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_query_key() -> str:
    return str(
        config_store.get("dashboard_query_key", "")
        or os.getenv("DASHBOARD_QUERY_KEY", "")
    ).strip()


def _require_query_key(x_query_key: Optional[str]) -> None:
    expected = _resolve_query_key()
    if not expected:
        raise HTTPException(503, "dashboard_query_key 未配置")
    provided = str(x_query_key or "").strip()
    if not provided or provided != expected:
        raise HTTPException(401, "query key 无效")


@router.get("/summary")
def get_dashboard_summary(x_query_key: Optional[str] = Header(default=None)):
    _require_query_key(x_query_key)

    active_tasks = [
        item
        for item in _task_store.list_snapshots()
        if str(item.get("status") or "") in {"pending", "running"}
    ]
    today_start = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    recent_24h = _utcnow() - timedelta(hours=24)

    with Session(engine) as session:
        total_accounts = int(
            session.exec(select(func.count()).select_from(AccountModel)).one() or 0
        )
        today_new_accounts = int(
            session.exec(
                select(func.count())
                .select_from(AccountModel)
                .where(AccountModel.created_at >= today_start)
            ).one()
            or 0
        )
        platform_rows = session.exec(
            select(AccountModel.platform, func.count()).group_by(AccountModel.platform)
        ).all()
        status_rows = session.exec(
            select(AccountModel.status, func.count()).group_by(AccountModel.status)
        ).all()
        today_success_logs = int(
            session.exec(
                select(func.count())
                .select_from(TaskLog)
                .where(TaskLog.status == "success")
                .where(TaskLog.created_at >= today_start)
            ).one()
            or 0
        )
        today_failed_logs = int(
            session.exec(
                select(func.count())
                .select_from(TaskLog)
                .where(TaskLog.status == "failed")
                .where(TaskLog.created_at >= today_start)
            ).one()
            or 0
        )
        recent_failed = int(
            session.exec(
                select(func.count())
                .select_from(TaskLog)
                .where(TaskLog.status == "failed")
                .where(TaskLog.created_at >= recent_24h)
            ).one()
            or 0
        )
        outlook_total = int(
            session.exec(select(func.count()).select_from(OutlookAccountModel)).one() or 0
        )
        outlook_enabled = int(
            session.exec(
                select(func.count())
                .select_from(OutlookAccountModel)
                .where(OutlookAccountModel.enabled == True)  # noqa: E712
            ).one()
            or 0
        )

    return {
        "ok": True,
        "data": {
            "generated_at": _utcnow().isoformat(),
            "accounts": {
                "total": total_accounts,
                "today_new": today_new_accounts,
                "by_platform": {
                    str(name): int(count or 0) for name, count in platform_rows
                },
                "by_status": {
                    str(name): int(count or 0) for name, count in status_rows
                },
            },
            "tasks": {
                "active_count": len(active_tasks),
                "today_success": today_success_logs,
                "today_failed": today_failed_logs,
                "recent_failed_24h": recent_failed,
            },
            "mailpool": {
                "outlook_total": outlook_total,
                "outlook_enabled": outlook_enabled,
            },
        },
    }


@router.get("/tasks")
def get_dashboard_tasks(
    limit: int = 20,
    x_query_key: Optional[str] = Header(default=None),
):
    _require_query_key(x_query_key)
    limit = max(1, min(int(limit or 20), 100))

    active_items = [
        {
            "id": item.get("id"),
            "platform": item.get("platform"),
            "status": item.get("status"),
            "progress": item.get("progress"),
            "success": item.get("success"),
            "skipped": item.get("skipped"),
            "failed": item.get("failed"),
            "progress_percent": item.get("progress_percent"),
            "source": item.get("source"),
        }
        for item in _task_store.list_snapshots()
        if str(item.get("status") or "") in {"pending", "running"}
    ]

    with Session(engine) as session:
        recent_logs = session.exec(
            select(TaskLog).order_by(TaskLog.id.desc()).limit(limit)
        ).all()

    return {
        "ok": True,
        "data": {
            "active": active_items,
            "recent_logs": [
                {
                    "id": int(item.id or 0),
                    "platform": item.platform,
                    "email": item.email,
                    "status": item.status,
                    "error": item.error,
                    "created_at": item.created_at.isoformat() if item.created_at else "",
                }
                for item in recent_logs
            ],
        },
    }
