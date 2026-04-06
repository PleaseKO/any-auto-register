from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select, func
from typing import Optional
from copy import deepcopy
from core.db import TaskLog, FailedEmailReimportEventModel, engine
from core.task_logs import build_failed_email_row, task_log_import_record
from core.task_runtime import (
    AttemptOutcome,
    AttemptResult,
    RegisterTaskStore,
    SkipCurrentAttemptRequested,
    StopTaskRequested,
)
import time, json, asyncio, threading, logging

router = APIRouter(prefix="/tasks", tags=["tasks"])
logger = logging.getLogger(__name__)

MAX_FINISHED_TASKS = 200
CLEANUP_THRESHOLD = 250
_task_store = RegisterTaskStore(
    max_finished_tasks=MAX_FINISHED_TASKS,
    cleanup_threshold=CLEANUP_THRESHOLD,
)


class RegisterTaskRequest(BaseModel):
    platform: str
    email: Optional[str] = None
    password: Optional[str] = None
    count: int = 1
    concurrency: int = 1
    register_delay_seconds: float = 0
    proxy: Optional[str] = None
    executor_type: str = "protocol"
    captcha_solver: str = "yescaptcha"
    extra: dict = Field(default_factory=dict)


class TaskLogBatchDeleteRequest(BaseModel):
    ids: list[int]


class FailedEmailRetryRow(BaseModel):
    email: str
    password: str = ""
    client_id: str = ""
    refresh_token: str = ""
    fail_count: int
    platform_count: int
    platforms: list[str]
    latest_log_id: int
    latest_error: str = ""
    latest_reason: str = ""
    latest_created_at: Optional[str] = None
    importable: bool = False
    has_oauth: bool = False
    source_mode: str = ""
    source_label: str = ""
    reimport_attempts: int = 0
    reimport_imported_count: int = 0
    reimport_duplicate_count: int = 0
    success_after_reimport: bool = False
    reimports_before_success: int = 0
    first_success_at: Optional[str] = None


class FailedEmailRetrySummaryResponse(BaseModel):
    total: int
    min_retry_count: int
    items: list[FailedEmailRetryRow]


class ReimportSuccessSummaryResponse(BaseModel):
    total: int
    items: list[FailedEmailRetryRow]


def _ensure_task_exists(task_id: str) -> None:
    if not _task_store.exists(task_id):
        raise HTTPException(404, "任务不存在")


def _ensure_task_mutable(task_id: str) -> None:
    _ensure_task_exists(task_id)
    snapshot = _task_store.snapshot(task_id)
    if snapshot.get("status") in {"done", "failed", "stopped"}:
        raise HTTPException(409, "任务已结束，无法再执行控制操作")


def _prepare_register_request(req: RegisterTaskRequest) -> RegisterTaskRequest:
    from core.config_store import config_store

    req_data = req.model_dump()
    req_data["extra"] = deepcopy(req_data.get("extra") or {})
    prepared = RegisterTaskRequest(**req_data)

    mail_provider = prepared.extra.get("mail_provider") or config_store.get(
        "mail_provider", ""
    )
    if mail_provider == "luckmail":
        platform = prepared.platform
        if platform in ("tavily", "openblocklabs"):
            raise HTTPException(400, f"LuckMail 渠道暂时不支持 {platform} 项目注册")

        mapping = {
            "trae": "trae",
            "cursor": "cursor",
            "grok": "grok",
            "kiro": "kiro",
            "chatgpt": "openai",
        }
        prepared.extra["luckmail_project_code"] = mapping.get(platform, platform)

    return prepared


def _create_task_record(
    task_id: str, req: RegisterTaskRequest, source: str, meta: dict | None = None
):
    _task_store.create(
        task_id,
        platform=req.platform,
        total=req.count,
        source=source,
        meta=meta,
    )


def enqueue_register_task(
    req: RegisterTaskRequest,
    *,
    background_tasks: BackgroundTasks | None = None,
    source: str = "manual",
    meta: dict | None = None,
) -> str:
    prepared = _prepare_register_request(req)
    task_id = f"task_{int(time.time() * 1000)}"
    _create_task_record(task_id, prepared, source, meta)
    if background_tasks is None:
        thread = threading.Thread(
            target=_run_register, args=(task_id, prepared), daemon=True
        )
        thread.start()
    else:
        background_tasks.add_task(_run_register, task_id, prepared)
    return task_id


def has_active_register_task(
    *, platform: str | None = None, source: str | None = None
) -> bool:
    return _task_store.has_active(platform=platform, source=source)


def _log(task_id: str, msg: str):
    """向任务追加一条日志"""
    ts = time.strftime("%H:%M:%S")
    entry = f"[{ts}] {msg}"
    _task_store.append_log(task_id, entry)
    print(entry)


def _save_task_log(
    platform: str, email: str, status: str, error: str = "", detail: dict = None
):
    """Write a TaskLog record to the database."""
    with Session(engine) as s:
        log = TaskLog(
            platform=platform,
            email=email,
            status=status,
            error=error,
            detail_json=json.dumps(detail or {}, ensure_ascii=False),
        )
        s.add(log)
        s.commit()


def _query_task_logs(
    *,
    platform: str | None = None,
    status: str | None = None,
    ids: list[int] | None = None,
):
    with Session(engine) as s:
        q = _build_task_log_query(platform=platform, status=status, ids=ids)
        q = q.order_by(TaskLog.id.desc())
        return s.exec(q).all()


def _build_task_log_query(
    *,
    platform: str | None = None,
    status: str | None = None,
    ids: list[int] | None = None,
):
    q = select(TaskLog)
    if platform:
        q = q.where(TaskLog.platform == platform)
    if status:
        q = q.where(TaskLog.status == status)
    if ids:
        q = q.where(TaskLog.id.in_(ids))
    return q


def _build_task_log_detail(
    task_id: str,
    req: RegisterTaskRequest,
    *,
    proxy: str | None = None,
    email: str = "",
    password: str | None = None,
    error: str = "",
) -> dict:
    return {
        "task_id": task_id,
        "platform": req.platform,
        "email": email or req.email or "",
        "password": password or req.password or "",
        "proxy": proxy or req.proxy or "",
        "executor_type": req.executor_type,
        "captcha_solver": req.captcha_solver,
        "extra": deepcopy(req.extra or {}),
        "error": error or "",
    }


def _best_effort_attempt_state(
    req: RegisterTaskRequest,
    *,
    platform=None,
    mailbox=None,
    current_email: str = "",
    password: str | None = None,
) -> tuple[str, str, dict]:
    email_value = str(current_email or req.email or "").strip()
    password_value = str(password or req.password or "").strip()
    extra_patch: dict = {}

    if platform is not None:
        for attr in ("_email", "email"):
            candidate = str(getattr(platform, attr, "") or "").strip()
            if candidate and not email_value:
                email_value = candidate
                break

        for attr in ("_password", "password"):
            candidate = str(getattr(platform, attr, "") or "").strip()
            if candidate and not password_value:
                password_value = candidate
                break

        acct = getattr(platform, "_acct", None)
        acct_email = str(getattr(acct, "email", "") or "").strip()
        if acct_email and not email_value:
            email_value = acct_email

    if mailbox is not None:
        mailbox_email = str(getattr(mailbox, "_email", "") or "").strip()
        if mailbox_email and not email_value:
            email_value = mailbox_email

        last_payload = getattr(mailbox, "_last_account_payload", None) or {}
        payload_email = str(last_payload.get("email") or "").strip()
        payload_password = str(last_payload.get("password") or "").strip()
        payload_client_id = str(last_payload.get("client_id") or "").strip()
        payload_refresh_token = str(last_payload.get("refresh_token") or "").strip()

        if payload_email and not email_value:
            email_value = payload_email
        if payload_password and not password_value:
            password_value = payload_password
        if payload_client_id:
            extra_patch["client_id"] = payload_client_id
        if payload_refresh_token:
            extra_patch["refresh_token"] = payload_refresh_token

    return email_value, password_value, extra_patch


def _auto_upload_integrations(task_id: str, account):
    """注册成功后自动导入外部系统。"""
    try:
        from services.external_sync import sync_account

        for result in sync_account(account):
            name = result.get("name", "Auto Upload")
            ok = bool(result.get("ok"))
            msg = result.get("msg", "")
            _log(task_id, f"  [{name}] {'[OK] ' + msg if ok else '[FAIL] ' + msg}")
    except Exception as e:
        _log(task_id, f"  [Auto Upload] 自动导入异常: {e}")


def _run_register(task_id: str, req: RegisterTaskRequest):
    from core.registry import get
    from core.base_platform import RegisterConfig
    from core.db import save_account
    from core.base_mailbox import create_mailbox
    from core.proxy_utils import normalize_proxy_url

    control = _task_store.control_for(task_id)
    _task_store.mark_running(task_id)
    success = 0
    skipped = 0
    errors = []
    start_gate_lock = threading.Lock()
    next_start_time = time.time()

    def _sleep_with_control(
        wait_seconds: float,
        *,
        attempt_id: int | None = None,
    ) -> None:
        remaining = max(float(wait_seconds or 0), 0.0)
        while remaining > 0:
            control.checkpoint(attempt_id=attempt_id)
            chunk = min(0.25, remaining)
            time.sleep(chunk)
            remaining -= chunk

    try:
        PlatformCls = get(req.platform)

        def _build_mailbox(proxy: Optional[str]):
            from core.config_store import config_store

            merged_extra = config_store.get_all().copy()
            merged_extra.update(
                {k: v for k, v in req.extra.items() if v is not None and v != ""}
            )
            return create_mailbox(
                provider=merged_extra.get("mail_provider", "luckmail"),
                extra=merged_extra,
                proxy=proxy,
            )

        def _do_one(i: int):
            nonlocal next_start_time
            proxy_pool = None
            _proxy = None
            current_email = req.email or ""
            attempt_id: int | None = None
            _mailbox = None
            _platform = None
            try:
                from core.proxy_pool import proxy_pool

                control.checkpoint()
                attempt_id = control.start_attempt()
                control.checkpoint(attempt_id=attempt_id)
                _proxy = req.proxy
                if not _proxy:
                    _proxy = proxy_pool.get_next()
                _proxy = normalize_proxy_url(_proxy)
                if req.register_delay_seconds > 0:
                    with start_gate_lock:
                        control.checkpoint(attempt_id=attempt_id)
                        now = time.time()
                        wait_seconds = max(0.0, next_start_time - now)
                        if wait_seconds > 0:
                            _log(
                                task_id,
                                f"第 {i + 1} 个账号启动前延迟 {wait_seconds:g} 秒",
                            )
                            _sleep_with_control(
                                wait_seconds,
                                attempt_id=attempt_id,
                            )
                        next_start_time = time.time() + req.register_delay_seconds
                control.checkpoint(attempt_id=attempt_id)
                from core.config_store import config_store

                merged_extra = config_store.get_all().copy()
                merged_extra.update(
                    {k: v for k, v in req.extra.items() if v is not None and v != ""}
                )

                _config = RegisterConfig(
                    executor_type=req.executor_type,
                    captcha_solver=req.captcha_solver,
                    proxy=_proxy,
                    extra=merged_extra,
                )
                _mailbox = _build_mailbox(_proxy)
                _platform = PlatformCls(config=_config, mailbox=_mailbox)
                _platform._task_attempt_token = attempt_id
                _platform._log_fn = lambda msg: _log(task_id, msg)
                _platform.bind_task_control(control)
                if getattr(_platform, "mailbox", None) is not None:
                    _platform.mailbox._task_attempt_token = attempt_id
                    _platform.mailbox._log_fn = _platform._log_fn
                _task_store.set_progress(task_id, f"{i + 1}/{req.count}")
                _log(task_id, f"开始注册第 {i + 1}/{req.count} 个账号")
                if _proxy:
                    _log(task_id, f"使用代理: {_proxy}")
                account = _platform.register(
                    email=req.email or None,
                    password=req.password,
                )
                current_email = account.email or current_email
                if isinstance(account.extra, dict):
                    mail_provider = merged_extra.get("mail_provider", "")
                    if mail_provider:
                        account.extra.setdefault("mail_provider", mail_provider)
                    if mail_provider == "luckmail" and req.platform == "chatgpt":
                        mailbox_token = getattr(_mailbox, "_token", "") or ""
                        if mailbox_token:
                            account.extra.setdefault("mailbox_token", mailbox_token)
                        if merged_extra.get("luckmail_project_code"):
                            account.extra.setdefault(
                                "luckmail_project_code",
                                merged_extra.get("luckmail_project_code"),
                            )
                        if merged_extra.get("luckmail_email_type"):
                            account.extra.setdefault(
                                "luckmail_email_type",
                                merged_extra.get("luckmail_email_type"),
                            )
                        if merged_extra.get("luckmail_domain"):
                            account.extra.setdefault(
                                "luckmail_domain", merged_extra.get("luckmail_domain")
                            )
                        if merged_extra.get("luckmail_base_url"):
                            account.extra.setdefault(
                                "luckmail_base_url",
                                merged_extra.get("luckmail_base_url"),
                            )
                saved_account = save_account(account)
                if _proxy:
                    proxy_pool.report_success(_proxy)
                _log(task_id, f"[OK] 注册成功: {account.email}")
                _save_task_log(
                    req.platform,
                    account.email,
                    "success",
                    detail=_build_task_log_detail(
                        task_id,
                        req,
                        proxy=_proxy,
                        email=account.email,
                        password=account.password,
                    ),
                )
                _auto_upload_integrations(task_id, saved_account or account)
                cashier_url = (account.extra or {}).get("cashier_url", "")
                if cashier_url:
                    _log(task_id, f"  [升级链接] {cashier_url}")
                    _task_store.add_cashier_url(task_id, cashier_url)
                return AttemptResult.success()
            except SkipCurrentAttemptRequested as e:
                resolved_email, resolved_password, extra_patch = _best_effort_attempt_state(
                    req,
                    platform=_platform,
                    mailbox=_mailbox,
                    current_email=current_email,
                )
                _log(task_id, f"[SKIP] 已跳过当前账号: {e}")
                _save_task_log(
                    req.platform,
                    resolved_email,
                    "skipped",
                    error=str(e),
                    detail=_build_task_log_detail(
                        task_id,
                        req,
                        proxy=_proxy,
                        email=resolved_email,
                        password=resolved_password,
                        error=str(e),
                    )
                    | {"extra": {**(req.extra or {}), **extra_patch}},
                )
                return AttemptResult.skipped(str(e))
            except StopTaskRequested as e:
                _log(task_id, f"[STOP] {e}")
                return AttemptResult.stopped(str(e))
            except Exception as e:
                resolved_email, resolved_password, extra_patch = _best_effort_attempt_state(
                    req,
                    platform=_platform,
                    mailbox=_mailbox,
                    current_email=current_email,
                    password=req.password,
                )
                if _proxy and proxy_pool is not None:
                    proxy_pool.report_fail(_proxy)
                _log(task_id, f"[FAIL] 注册失败: {e}")
                _save_task_log(
                    req.platform,
                    resolved_email,
                    "failed",
                    error=str(e),
                    detail=_build_task_log_detail(
                        task_id,
                        req,
                        proxy=_proxy,
                        email=resolved_email,
                        password=resolved_password,
                        error=str(e),
                    )
                    | {"extra": {**(req.extra or {}), **extra_patch}},
                )
                return AttemptResult.failed(str(e))
            finally:
                control.finish_attempt(attempt_id)

        from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed

        max_workers = min(req.concurrency, req.count, 20)
        stopped = False
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_do_one, i) for i in range(req.count)]
            for f in as_completed(futures):
                try:
                    result = f.result()
                except CancelledError:
                    continue
                except Exception as e:
                    _log(task_id, f"[ERROR] 任务线程异常: {e}")
                    errors.append(str(e))
                    continue
                if result.outcome == AttemptOutcome.SUCCESS:
                    success += 1
                elif result.outcome == AttemptOutcome.SKIPPED:
                    skipped += 1
                elif result.outcome == AttemptOutcome.STOPPED:
                    stopped = True
                else:
                    errors.append(result.message)
                _task_store.update_result_counts(
                    task_id,
                    success=success,
                    skipped=skipped,
                    errors=errors,
                )
                if stopped or control.is_stop_requested():
                    stopped = True
                    for pending in futures:
                        if pending is not f:
                            pending.cancel()
    except Exception as e:
        _log(task_id, f"致命错误: {e}")
        _task_store.finish(
            task_id,
            status="failed",
            success=success,
            skipped=skipped,
            errors=errors,
            error=str(e),
        )
        _task_store.cleanup()
        return

    final_status = "stopped" if control.is_stop_requested() or stopped else "done"
    if final_status == "stopped":
        summary = (
            f"任务已停止: 成功 {success} 个, 跳过 {skipped} 个, 失败 {len(errors)} 个"
        )
    else:
        summary = f"完成: 成功 {success} 个, 跳过 {skipped} 个, 失败 {len(errors)} 个"
    _log(task_id, summary)
    _task_store.finish(
        task_id,
        status=final_status,
        success=success,
        skipped=skipped,
        errors=errors,
    )
    _task_store.cleanup()


@router.post("/register")
def create_register_task(
    req: RegisterTaskRequest,
    background_tasks: BackgroundTasks,
):
    task_id = enqueue_register_task(req, background_tasks=background_tasks)
    return {"task_id": task_id}


@router.post("/{task_id}/skip-current")
def skip_current_account(task_id: str):
    _ensure_task_mutable(task_id)
    control = _task_store.request_skip_current(task_id)
    _log(task_id, "收到手动跳过当前账号请求")
    return {"ok": True, "task_id": task_id, "control": control}


@router.post("/{task_id}/stop")
def stop_task(task_id: str):
    _ensure_task_mutable(task_id)
    control = _task_store.request_stop(task_id)
    _log(task_id, "收到手动停止任务请求")
    return {"ok": True, "task_id": task_id, "control": control}


@router.get("/logs")
def get_logs(
    platform: str = None,
    status: str = None,
    page: int = 1,
    page_size: int = 50,
):
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 50), 1), 500)
    offset = (page - 1) * page_size
    with Session(engine) as s:
        base_query = _build_task_log_query(platform=platform, status=status)
        count_query = select(func.count()).select_from(TaskLog)
        if platform:
            count_query = count_query.where(TaskLog.platform == platform)
        if status:
            count_query = count_query.where(TaskLog.status == status)
        total = int(s.exec(count_query).one() or 0)
        items = s.exec(
            base_query.order_by(TaskLog.id.desc()).offset(offset).limit(page_size)
        ).all()
    return {"total": total, "items": items}


@router.get("/logs/summary")
def get_logs_summary():
    with Session(engine) as s:
        rows = s.exec(
            select(TaskLog.status, func.count())
            .group_by(TaskLog.status)
        ).all()
        failed_items = s.exec(
            select(TaskLog).where(TaskLog.status == "failed").order_by(TaskLog.id.desc())
        ).all()

    by_status = {str(status): int(count or 0) for status, count in rows}
    failed_rows = [build_failed_email_row(item) for item in failed_items]
    importable = sum(1 for row in failed_rows if row["importable"])
    inferred = sum(1 for row in failed_rows if row["source_mode"])
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "failed_email_pool": {
            "total": len(failed_rows),
            "importable": importable,
            "inferred": inferred,
        },
    }


@router.get("/failed-emails")
def get_failed_emails(
    platform: str = "",
    q: str = "",
    page: int = 1,
    page_size: int = 50,
    dedupe: bool = True,
    importable_only: bool = False,
):
    items = _query_task_logs(platform=platform or None, status="failed")
    rows = [build_failed_email_row(item) for item in items]

    keyword = str(q or "").strip().lower()
    if keyword:
        rows = [
            row
            for row in rows
            if keyword in f"{row['email']} {row['platform']} {row['error']} {row['reason']}".lower()
        ]

    if importable_only:
        rows = [row for row in rows if row["importable"]]

    if dedupe:
        deduped: dict[str, dict] = {}
        for row in rows:
            email_key = str(row["email"] or "").strip().lower()
            if not email_key:
                deduped[f"__empty__{row['id']}"] = row
                continue
            existing = deduped.get(email_key)
            if existing is None or int(row["id"]) > int(existing["id"]):
                deduped[email_key] = row
        rows = sorted(deduped.values(), key=lambda x: int(x["id"]), reverse=True)

    total = len(rows)
    importable_count = sum(1 for row in rows if row["importable"])
    inferred_count = sum(1 for row in rows if row["source_mode"])
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 50), 1), 500)
    offset = (page - 1) * page_size
    paged = rows[offset : offset + page_size]
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "importable": importable_count,
        "inferred": inferred_count,
        "items": paged,
    }


@router.get("/failed-emails/retry-summary", response_model=FailedEmailRetrySummaryResponse)
def get_failed_email_retry_summary(
    q: str = "",
    platform: str = "",
    min_retry_count: int = 3,
    exact_retry_count: int = 0,
):
    items = _query_task_logs(platform=platform or None, status="failed")
    with Session(engine) as s:
        success_items = s.exec(select(TaskLog).where(TaskLog.status == "success")).all()
        reimport_events = s.exec(select(FailedEmailReimportEventModel)).all()
    grouped: dict[str, dict] = {}
    success_map: dict[str, list] = {}
    reimport_map: dict[str, list] = {}

    for item in success_items:
        email_key = str(getattr(item, "email", "") or "").strip().lower()
        if not email_key:
            continue
        success_map.setdefault(email_key, []).append(item)

    for event in reimport_events:
        email_key = str(getattr(event, "email", "") or "").strip().lower()
        if not email_key:
            continue
        reimport_map.setdefault(email_key, []).append(event)

    for item in items:
        row = build_failed_email_row(item)
        email_key = str(row["email"] or "").strip().lower()
        if not email_key:
            continue

        payload = grouped.get(email_key)
        if payload is None:
            payload = {
                "email": str(row["email"] or "").strip(),
                "fail_count": 0,
                "platforms": set(),
                "latest_log_id": int(row["id"] or 0),
                "password": str(row["password"] or "").strip(),
                "client_id": str(row["client_id"] or "").strip(),
                "refresh_token": str(row["refresh_token"] or "").strip(),
                "latest_error": str(row["error"] or "").strip(),
                "latest_reason": str(row["reason"] or "").strip(),
                "latest_created_at": row["created_at"].isoformat() if row.get("created_at") else None,
                "importable": bool(row["importable"]),
                "has_oauth": bool(row["has_oauth"]),
                "source_mode": str(row["source_mode"] or "").strip(),
                "source_label": str(row["source_label"] or "").strip(),
                "reimport_attempts": 0,
                "reimport_imported_count": 0,
                "reimport_duplicate_count": 0,
                "success_after_reimport": False,
                "reimports_before_success": 0,
                "first_success_at": None,
            }
            grouped[email_key] = payload

        payload["fail_count"] += 1
        platform_name = str(row["platform"] or "").strip()
        if platform_name:
            payload["platforms"].add(platform_name)

        if int(row["id"] or 0) >= int(payload["latest_log_id"] or 0):
            payload["latest_log_id"] = int(row["id"] or 0)
            payload["password"] = str(row["password"] or "").strip()
            payload["client_id"] = str(row["client_id"] or "").strip()
            payload["refresh_token"] = str(row["refresh_token"] or "").strip()
            payload["latest_error"] = str(row["error"] or "").strip()
            payload["latest_reason"] = str(row["reason"] or "").strip()
            payload["latest_created_at"] = row["created_at"].isoformat() if row.get("created_at") else None
            payload["importable"] = bool(row["importable"])
            payload["has_oauth"] = bool(row["has_oauth"])
            payload["source_mode"] = str(row["source_mode"] or "").strip()
            payload["source_label"] = str(row["source_label"] or "").strip()

    keyword = str(q or "").strip().lower()
    exact_count = max(int(exact_retry_count or 0), 0)
    threshold = max(int(min_retry_count or 3), 1)
    rows: list[dict] = []

    for payload in grouped.values():
        fail_count = int(payload["fail_count"] or 0)
        if exact_count > 0:
            if fail_count != exact_count:
                continue
        elif fail_count < threshold:
            continue
        platforms = sorted(str(name) for name in payload["platforms"] if str(name).strip())
        haystack = " ".join(
            [
                str(payload["email"] or ""),
                str(payload["latest_error"] or ""),
                str(payload["latest_reason"] or ""),
                " ".join(platforms),
            ]
        ).lower()
        if keyword and keyword not in haystack:
            continue
        rows.append(
            {
                "email": payload["email"],
                "password": payload["password"],
                "client_id": payload["client_id"],
                "refresh_token": payload["refresh_token"],
                "fail_count": fail_count,
                "platform_count": len(platforms),
                "platforms": platforms,
                "latest_log_id": int(payload["latest_log_id"] or 0),
                "latest_error": payload["latest_error"],
                "latest_reason": payload["latest_reason"],
                "latest_created_at": payload["latest_created_at"],
                "importable": bool(payload["importable"]),
                "has_oauth": bool(payload["has_oauth"]),
                "source_mode": payload["source_mode"],
                "source_label": payload["source_label"],
                "reimport_attempts": 0,
                "reimport_imported_count": 0,
                "reimport_duplicate_count": 0,
                "success_after_reimport": False,
                "reimports_before_success": 0,
                "first_success_at": None,
            }
        )

    for row in rows:
        email_key = str(row["email"] or "").strip().lower()
        events = sorted(
            reimport_map.get(email_key, []),
            key=lambda item: getattr(item, "created_at", None) or "",
        )
        successes = sorted(
            success_map.get(email_key, []),
            key=lambda item: getattr(item, "created_at", None) or "",
        )

        row["reimport_attempts"] = len(events)
        row["reimport_imported_count"] = sum(
            1 for event in events if str(getattr(event, "result", "") or "").strip() == "imported"
        )
        row["reimport_duplicate_count"] = sum(
            1 for event in events if str(getattr(event, "result", "") or "").strip() == "duplicate"
        )

        if not events or not successes:
            continue

        first_reimport_at = getattr(events[0], "created_at", None)
        success_after_reimport = None
        for success_item in successes:
            success_at = getattr(success_item, "created_at", None)
            if first_reimport_at and success_at and success_at >= first_reimport_at:
                success_after_reimport = success_item
                break

        if success_after_reimport is None:
            continue

        success_at = getattr(success_after_reimport, "created_at", None)
        row["success_after_reimport"] = True
        row["first_success_at"] = success_at.isoformat() if success_at else None
        row["reimports_before_success"] = sum(
            1
            for event in events
            if getattr(event, "created_at", None) and success_at and getattr(event, "created_at", None) <= success_at
        )

    rows.sort(key=lambda item: (-int(item["fail_count"]), -int(item["latest_log_id"])))
    return {
        "total": len(rows),
        "min_retry_count": exact_count or threshold,
        "items": rows,
    }


@router.get("/failed-emails/reimport-success", response_model=ReimportSuccessSummaryResponse)
def get_reimport_success_summary(
    q: str = "",
    platform: str = "",
):
    summary = get_failed_email_retry_summary(
        q=q,
        platform=platform,
        min_retry_count=1,
        exact_retry_count=0,
    )
    items = [
        item
        for item in summary["items"]
        if bool(item.get("success_after_reimport"))
    ]
    items.sort(
        key=lambda item: (
            -int(item.get("reimports_before_success") or 0),
            str(item.get("first_success_at") or ""),
        ),
        reverse=False,
    )
    return {
        "total": len(items),
        "items": items,
    }


@router.get("/logs/export")
def export_logs(
    format: str = "txt",
    platform: str = None,
    status: str = "failed",
    ids: str = "",
):
    parsed_ids: list[int] = []
    if ids:
        for part in str(ids).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                parsed_ids.append(int(part))
            except ValueError:
                raise HTTPException(400, f"无效的日志 ID: {part}")

    items = _query_task_logs(
        platform=platform,
        status=status,
        ids=parsed_ids or None,
    )
    if not items:
        raise HTTPException(404, "没有可导出的记录")

    normalized_format = str(format or "txt").strip().lower()
    if normalized_format not in {"txt", "json"}:
        raise HTTPException(400, "仅支持 txt 或 json 导出")

    records = []
    for item in items:
        record = task_log_import_record(item)
        records.append(
            {
                "email": record["email"],
                "password": record["password"],
                "clientId": record["client_id"],
                "refreshToken": record["refresh_token"],
            }
        )

    if normalized_format == "txt":
        lines = []
        for item in records:
            if not item["email"]:
                continue
            parts = [item["email"], item["password"]]
            if item["clientId"] or item["refreshToken"]:
                parts.extend([item["clientId"], item["refreshToken"]])
            lines.append("----".join(parts))
        content = "\n".join(lines)
        filename = "failed_accounts_import.txt"
        media_type = "text/plain; charset=utf-8"
    else:
        content = json.dumps(
            [{k: v for k, v in item.items() if v} for item in records if item["email"]],
            ensure_ascii=False,
            indent=2,
        )
        filename = "failed_accounts_import.json"
        media_type = "application/json; charset=utf-8"

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/logs/batch-delete")
def batch_delete_logs(body: TaskLogBatchDeleteRequest):
    if not body.ids:
        raise HTTPException(400, "任务历史 ID 列表不能为空")

    unique_ids = list(dict.fromkeys(body.ids))
    if len(unique_ids) > 1000:
        raise HTTPException(400, "单次最多删除 1000 条任务历史")

    with Session(engine) as s:
        try:
            logs = s.exec(select(TaskLog).where(TaskLog.id.in_(unique_ids))).all()
            found_ids = {log.id for log in logs if log.id is not None}

            for log in logs:
                s.delete(log)

            s.commit()
            deleted_count = len(found_ids)
            not_found_ids = [log_id for log_id in unique_ids if log_id not in found_ids]
            logger.info("批量删除任务历史成功: %s 条", deleted_count)

            return {
                "deleted": deleted_count,
                "not_found": not_found_ids,
                "total_requested": len(unique_ids),
            }
        except Exception as e:
            s.rollback()
            logger.exception("批量删除任务历史失败")
            raise HTTPException(500, f"批量删除任务历史失败: {str(e)}")


@router.post("/logs/{log_id}/retry")
def retry_failed_log(log_id: int, background_tasks: BackgroundTasks):
    with Session(engine) as s:
        log = s.get(TaskLog, log_id)
        if not log:
            raise HTTPException(404, "失败记录不存在")

    try:
        detail = json.loads(log.detail_json or "{}")
    except Exception:
        detail = {}

    platform = str(detail.get("platform") or log.platform or "").strip()
    if not platform:
        raise HTTPException(400, "失败记录缺少平台信息，无法重试")

    req = RegisterTaskRequest(
        platform=platform,
        email=str(detail.get("email") or log.email or "").strip() or None,
        password=str(detail.get("password") or "").strip() or None,
        count=1,
        concurrency=1,
        register_delay_seconds=0,
        proxy=str(detail.get("proxy") or "").strip() or None,
        executor_type=str(detail.get("executor_type") or "protocol").strip() or "protocol",
        captcha_solver=str(detail.get("captcha_solver") or "yescaptcha").strip() or "yescaptcha",
        extra=deepcopy(detail.get("extra") or {}),
    )
    task_id = enqueue_register_task(
        req,
        background_tasks=background_tasks,
        source="retry_failed_log",
        meta={"retry_from_log_id": log_id},
    )
    return {"ok": True, "task_id": task_id, "log_id": log_id}


@router.get("/{task_id}/logs/stream")
async def stream_logs(task_id: str, since: int = 0):
    """SSE 实时日志流"""
    _ensure_task_exists(task_id)

    async def event_generator():
        sent = since
        while True:
            logs, status = _task_store.log_state(task_id)
            while sent < len(logs):
                yield f"data: {json.dumps({'line': logs[sent]})}\n\n"
                sent += 1
            if status in ("done", "failed", "stopped"):
                yield f"data: {json.dumps({'done': True, 'status': status})}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{task_id}")
def get_task(task_id: str):
    _ensure_task_exists(task_id)
    return _task_store.snapshot(task_id)


@router.get("")
def list_tasks():
    return _task_store.list_snapshots()
