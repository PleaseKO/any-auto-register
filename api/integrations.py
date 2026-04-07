from __future__ import annotations

from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, BackgroundTasks
from pydantic import BaseModel, Field
from sqlmodel import Session, select, func

from core.base_platform import Account, AccountStatus
from core.db import AccountModel, engine
from services.external_apps import install, list_status, start, start_all, stop, stop_all
from services.chatgpt_sync import (
    backfill_chatgpt_account_to_cpa,
    backfill_chatgpt_account_to_sub2api,
    get_cliproxy_sync_state,
    get_sub2api_sync_state,
)

router = APIRouter(prefix="/integrations", tags=["integrations"])


class BackfillRequest(BaseModel):
    platforms: list[str] = Field(default_factory=lambda: ["grok", "kiro"])
    account_ids: list[int] = Field(default_factory=list)
    pending_only: bool = False
    status: Optional[str] = None
    email: Optional[str] = None
    target: str = "cliproxyapi"


def _create_async_task(*, platform: str, total: int, source: str, meta: dict | None = None) -> str:
    from api.tasks import _task_store
    import time

    task_id = f"task_{int(time.time() * 1000)}"
    _task_store.create(task_id, platform=platform, total=total, source=source, meta=meta or {})
    return task_id


def _task_log(task_id: str, message: str) -> None:
    from api.tasks import _task_store
    import time

    _task_store.append_log(task_id, f"[{time.strftime('%H:%M:%S')}] {message}")


def _finish_async_task(task_id: str, *, success: int, skipped: int, errors: list[str], status: str = "done") -> None:
    from api.tasks import _task_store

    _task_store.finish(task_id, status=status, success=success, skipped=skipped, errors=errors)
    _task_store.cleanup()


def _retry_chatgpt_backfill(row: AccountModel, *, target: str, session: Session, retries: int = 3) -> dict:
    import time

    last_outcome: dict = {"ok": False, "message": "unknown", "results": []}
    for attempt in range(1, retries + 1):
        last_outcome = (
            backfill_chatgpt_account_to_sub2api(row, session=session, commit=True)
            if target == "sub2api"
            else backfill_chatgpt_account_to_cpa(row, session=session, commit=True)
        )
        if bool(last_outcome.get("ok")) or bool(last_outcome.get("skipped")):
            return last_outcome
        if attempt < retries:
            time.sleep(min(2 * attempt, 5))
    return last_outcome


def _to_account(model: AccountModel) -> Account:
    return Account(
        platform=model.platform,
        email=model.email,
        password=model.password,
        user_id=model.user_id,
        region=model.region,
        token=model.token,
        status=AccountStatus(model.status),
        extra=model.get_extra(),
    )


@router.get("/services")
def get_services():
    return {"items": list_status()}


@router.post("/services/start-all")
def start_all_services():
    return {"items": start_all()}


@router.post("/services/stop-all")
def stop_all_services():
    return {"items": stop_all()}


@router.post("/services/{name}/start")
def start_service(name: str):
    return start(name)


@router.post("/services/{name}/install")
def install_service(name: str):
    return install(name)


@router.post("/services/{name}/stop")
def stop_service(name: str):
    return stop(name)


@router.post("/backfill")
def backfill_integrations(body: BackfillRequest):
    summary = {"total": 0, "success": 0, "failed": 0, "skipped": 0, "items": []}
    targets = set(body.platforms or [])
    target = str(body.target or "cliproxyapi").strip().lower() or "cliproxyapi"

    with Session(engine) as s:
        q = select(AccountModel)
        if body.account_ids:
            q = q.where(AccountModel.id.in_(body.account_ids))
            if targets:
                q = q.where(AccountModel.platform.in_(targets))
        elif targets:
            q = q.where(AccountModel.platform.in_(targets))
        else:
            return summary

        if body.status:
            q = q.where(AccountModel.status == body.status)
        if body.email:
            q = q.where(AccountModel.email.contains(body.email))

        rows = s.exec(q).all()
        if body.pending_only:
            rows = [
                row for row in rows
                if row.platform != "chatgpt"
                or (
                    str(get_cliproxy_sync_state(row).get("remote_state") or "").strip().lower() == "not_found"
                    if target == "cliproxyapi"
                    else not bool(get_sub2api_sync_state(row).get("uploaded") or get_sub2api_sync_state(row).get("uploaded_at"))
                )
            ]

        if any(row.platform == "grok" for row in rows):
            from services.grok2api_runtime import ensure_grok2api_ready

            ok, msg = ensure_grok2api_ready()
            if not ok:
                return {
                    "total": 0,
                    "success": 0,
                    "failed": 0,
                    "items": [{"platform": "grok", "email": "", "results": [{"name": "grok2api", "ok": False, "msg": msg}]}],
                }

        for row in rows:
            item = {"platform": row.platform, "email": row.email, "results": []}
            try:
                results = []
                if row.platform == "chatgpt":
                    outcome = (
                        backfill_chatgpt_account_to_sub2api(row, session=s, commit=True)
                        if target == "sub2api"
                        else backfill_chatgpt_account_to_cpa(row, session=s, commit=True)
                    )
                    ok = bool(outcome.get("ok"))
                    skipped = bool(outcome.get("skipped"))
                    results.extend(outcome.get("results") or [])
                    if not results:
                        results.append(
                            {
                                "name": "Sub2API" if target == "sub2api" else "CLIProxyAPI",
                                "ok": ok,
                                "msg": outcome.get("message", ""),
                            }
                        )
                    if skipped:
                        summary["skipped"] += 1
                    elif ok:
                        summary["success"] += 1
                    else:
                        summary["failed"] += 1

                elif row.platform == "grok":
                    from core.config_store import config_store
                    from platforms.grok.grok2api_upload import upload_to_grok2api

                    account = _to_account(row)
                    api_url = str(config_store.get("grok2api_url", "") or "").strip() or "http://127.0.0.1:8011"
                    app_key = str(config_store.get("grok2api_app_key", "") or "").strip() or "grok2api"
                    ok, msg = upload_to_grok2api(account, api_url=api_url, app_key=app_key)
                    results.append({"name": "grok2api", "ok": ok, "msg": msg})

                elif row.platform == "kiro":
                    from core.config_store import config_store
                    from platforms.kiro.account_manager_upload import upload_to_kiro_manager

                    account = _to_account(row)
                    configured_path = str(config_store.get("kiro_manager_path", "") or "").strip() or None
                    ok, msg = upload_to_kiro_manager(account, path=configured_path)
                    results.append({"name": "Kiro Manager", "ok": ok, "msg": msg})

                if not results:
                    item["results"].append({"name": "skip", "ok": False, "msg": "未配置对应导入目标"})
                    summary["failed"] += 1
                else:
                    item["results"] = results
                    if row.platform != "chatgpt":
                        if all(r.get("ok") for r in results):
                            summary["success"] += 1
                        else:
                            summary["failed"] += 1
            except Exception as e:
                s.rollback()
                item["results"].append({"name": "error", "ok": False, "msg": str(e)})
                summary["failed"] += 1
            summary["items"].append(item)
            summary["total"] += 1

    return summary


def _run_backfill_integrations_task(task_id: str, body: BackfillRequest) -> None:
    from api.tasks import _task_store

    _task_store.mark_running(task_id)
    summary = {"total": 0, "success": 0, "failed": 0, "skipped": 0}
    targets = set(body.platforms or [])
    target = str(body.target or "cliproxyapi").strip().lower() or "cliproxyapi"
    errors: list[str] = []

    with Session(engine) as s:
        q = select(AccountModel)
        if body.account_ids:
            q = q.where(AccountModel.id.in_(body.account_ids))
            if targets:
                q = q.where(AccountModel.platform.in_(targets))
        elif targets:
            q = q.where(AccountModel.platform.in_(targets))
        else:
            _finish_async_task(task_id, success=0, skipped=0, errors=[], status="done")
            return

        if body.status:
            q = q.where(AccountModel.status == body.status)
        if body.email:
            q = q.where(AccountModel.email.contains(body.email))

        rows = s.exec(q).all()
        if body.pending_only:
            rows = [
                row for row in rows
                if row.platform != "chatgpt"
                or (
                    str(get_cliproxy_sync_state(row).get("remote_state") or "").strip().lower() == "not_found"
                    if target == "cliproxyapi"
                    else not bool(get_sub2api_sync_state(row).get("uploaded") or get_sub2api_sync_state(row).get("uploaded_at"))
                )
            ]

        total = len(rows)
        _task_store.set_progress(task_id, f"0/{total}")

        if target == "sub2api":
            task_rows = [(int(row.id or 0), str(row.email or "").strip(), str(row.platform or "").strip()) for row in rows]

            def _worker(account_id: int, email: str, platform: str) -> dict:
                if platform != "chatgpt":
                    return {"email": email, "status": "skipped", "message": "非 chatgpt 账号"}
                with Session(engine) as thread_session:
                    db_row = thread_session.get(AccountModel, account_id)
                    if not db_row:
                        return {"email": email, "status": "failed", "message": "账号不存在"}
                    outcome = _retry_chatgpt_backfill(db_row, target=target, session=thread_session, retries=3)
                    ok = bool(outcome.get("ok"))
                    skipped = bool(outcome.get("skipped"))
                    msg = str(outcome.get("message") or "")
                    if skipped:
                        return {"email": email, "status": "skipped", "message": msg}
                    if ok:
                        return {"email": email, "status": "success", "message": msg}
                    return {"email": email, "status": "failed", "message": msg}

            completed = 0
            max_workers = min(10, max(1, len(task_rows)))
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                future_map = {
                    pool.submit(_worker, account_id, email, platform): (account_id, email)
                    for account_id, email, platform in task_rows
                }
                for future in as_completed(future_map):
                    account_id, email = future_map[future]
                    try:
                        result = future.result()
                        status = str(result.get("status") or "failed")
                        msg = str(result.get("message") or "")
                    except Exception as exc:
                        status = "failed"
                        msg = str(exc)
                    if status == "success":
                        summary["success"] += 1
                        _task_log(task_id, f"[OK] {email} {msg}")
                    elif status == "skipped":
                        summary["skipped"] += 1
                        _task_log(task_id, f"[SKIP] {email} {msg}")
                    else:
                        summary["failed"] += 1
                        errors.append(f"{email}: {msg}")
                        _task_log(task_id, f"[FAIL] {email} {msg}")
                    completed += 1
                    summary["total"] += 1
                    _task_store.set_progress(task_id, f"{completed}/{total}")
        else:
            for index, row in enumerate(rows, start=1):
                try:
                    if row.platform == "chatgpt":
                        outcome = _retry_chatgpt_backfill(row, target=target, session=s, retries=3)
                        ok = bool(outcome.get("ok"))
                        skipped = bool(outcome.get("skipped"))
                        msg = str(outcome.get("message") or "")
                        if skipped:
                            summary["skipped"] += 1
                            _task_log(task_id, f"[SKIP] {row.email} {msg}")
                        elif ok:
                            summary["success"] += 1
                            _task_log(task_id, f"[OK] {row.email} {msg}")
                        else:
                            summary["failed"] += 1
                            errors.append(f"{row.email}: {msg}")
                            _task_log(task_id, f"[FAIL] {row.email} {msg}")
                    else:
                        summary["skipped"] += 1
                        _task_log(task_id, f"[SKIP] {row.email} 非 chatgpt 账号")
                except Exception as exc:
                    s.rollback()
                    summary["failed"] += 1
                    errors.append(f"{row.email}: {exc}")
                    _task_log(task_id, f"[FAIL] {row.email} {exc}")
                summary["total"] += 1
                _task_store.set_progress(task_id, f"{index}/{total}")

    _finish_async_task(task_id, success=summary["success"], skipped=summary["skipped"], errors=errors)


@router.post("/backfill/start")
def start_backfill_integrations(body: BackfillRequest, background_tasks: BackgroundTasks):
    with Session(engine) as s:
        q = select(func.count()).select_from(AccountModel)
        targets = set(body.platforms or [])
        if body.account_ids:
            q = q.where(AccountModel.id.in_(body.account_ids))
            if targets:
                q = q.where(AccountModel.platform.in_(targets))
        elif targets:
            q = q.where(AccountModel.platform.in_(targets))
        if body.status:
            q = q.where(AccountModel.status == body.status)
        if body.email:
            q = q.where(AccountModel.email.contains(body.email))
        total = int(s.exec(q).one() or 0)

    task_id = _create_async_task(
        platform=f"integration_backfill_{str(body.target or 'cliproxyapi').strip().lower() or 'cliproxyapi'}",
        total=total,
        source="manual_integration_backfill",
        meta={"request": body.model_dump()},
    )
    background_tasks.add_task(_run_backfill_integrations_task, task_id, body)
    return {"task_id": task_id}
