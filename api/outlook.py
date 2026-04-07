from datetime import datetime, timezone
import json
import io
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, BackgroundTasks, Body
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select, func

from core.db import engine, OutlookAccountModel, FailedEmailReimportEventModel

router = APIRouter(prefix="/outlook", tags=["outlook"])


def _utcnow():
    return datetime.now(timezone.utc)


class OutlookBatchImportRequest(BaseModel):
    data: str
    enabled: bool = True
    source: str = ""
    source_tag: str = ""


class OutlookBatchImportResponse(BaseModel):
    total: int
    success: int
    failed: int
    accounts: List[Dict[str, Any]]
    errors: List[str]

class OutlookAccountItem(BaseModel):
    id: int
    email: str
    enabled: bool
    has_oauth: bool
    source_tag: str = "manual"
    created_at: datetime
    updated_at: datetime
    last_used: Optional[datetime] = None


class OutlookListResponse(BaseModel):
    total: int
    items: List[OutlookAccountItem]


class OutlookBatchDeleteRequest(BaseModel):
    ids: List[int]


class OutlookHealthCheckRequest(BaseModel):
    ids: List[int] = []
    q: str = ""
    enabled: str = "true"
    source_tag: str = ""
    disable_invalid: bool = True
    limit: int = 200


class OutlookUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    password: Optional[str] = None
    client_id: Optional[str] = None
    refresh_token: Optional[str] = None


def _create_async_task(*, platform: str, total: int, source: str, meta: dict[str, Any] | None = None) -> str:
    from api.tasks import _task_store
    import time

    task_id = f"task_{int(time.time() * 1000)}"
    _task_store.create(task_id, platform=platform, total=total, source=source, meta=meta or {})
    return task_id


def _task_log(task_id: str, message: str) -> None:
    from api.tasks import _task_store
    import time

    entry = f"[{time.strftime('%H:%M:%S')}] {message}"
    _task_store.append_log(task_id, entry)


def _finish_async_task(task_id: str, *, success: int, skipped: int, errors: list[str], status: str = "done") -> None:
    from api.tasks import _task_store

    _task_store.finish(task_id, status=status, success=success, skipped=skipped, errors=errors)
    _task_store.cleanup()


def _retry_validate_outlook_account(mailbox, payload: dict, *, retries: int = 3) -> tuple[bool, str]:
    import time

    last_valid = False
    last_reason = "unknown"
    for attempt in range(1, retries + 1):
        valid, reason = mailbox._validate_claimed_account(payload)  # type: ignore[attr-defined]
        last_valid = bool(valid)
        last_reason = str(reason or "")
        if last_valid:
            return True, last_reason
        if attempt < retries:
            time.sleep(min(2 * attempt, 5))
    return last_valid, last_reason


def _dedupe_outlook_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    deduped: list[dict[str, Any]] = []
    errors: list[str] = []
    seen_emails: set[str] = set()

    for row in rows:
        email = str(row.get("email") or "").strip()
        line_no = row.get("line_no", "?")
        if "@" not in email:
            errors.append(f"行 {line_no}: 无效的邮箱地址: {email}")
            continue
        email_key = email.lower()
        if email_key in seen_emails:
            errors.append(f"行 {line_no}: 请求内重复邮箱: {email}")
            continue
        seen_emails.add(email_key)
        deduped.append(
            {
                "line_no": line_no,
                "email": email,
                "password": str(row.get("password") or "").strip(),
                "client_id": str(row.get("client_id") or "").strip(),
                "refresh_token": str(row.get("refresh_token") or "").strip(),
            }
        )

    return deduped, errors


def _parse_outlook_import_rows(data: str) -> tuple[int, list[dict[str, Any]], list[str]]:
    lines = (data or "").splitlines()
    total = len(lines)
    raw_rows: list[dict[str, Any]] = []
    errors: list[str] = []

    for idx, raw_line in enumerate(lines):
        line = str(raw_line or "").strip()
        if not line or line.startswith("#"):
            continue

        parts = [part.strip() for part in line.split("----")]
        if len(parts) < 2:
            errors.append(f"行 {idx + 1}: 格式错误，至少需要邮箱和密码")
            continue

        email = str(parts[0] or "").strip()
        password = str(parts[1] or "").strip()
        raw_rows.append(
            {
                "line_no": idx + 1,
                "email": email,
                "password": password,
                "client_id": parts[2] if len(parts) >= 3 else "",
                "refresh_token": parts[3] if len(parts) >= 4 else "",
            }
        )

    parsed_rows, dedupe_errors = _dedupe_outlook_rows(raw_rows)
    errors.extend(dedupe_errors)
    return total, parsed_rows, errors


def _parse_register_machine_upload_payload(
    payload: dict[str, Any] | None,
) -> tuple[int, list[dict[str, Any]], list[str]]:
    data = payload if isinstance(payload, dict) else {}
    raw_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    total = 0

    line_payload = data.get("data")
    if isinstance(line_payload, str) and line_payload.strip():
        parsed_total, parsed_rows, parsed_errors = _parse_outlook_import_rows(line_payload)
        total += parsed_total
        raw_rows.extend(parsed_rows)
        errors.extend(parsed_errors)

    def _pick(*keys: str) -> str:
        for key in keys:
            if key not in data:
                continue
            value = data.get(key)
            if value is None:
                continue
            return str(value).strip()
        return ""

    account = _pick("a", "account", "email")
    password = _pick("p", "password")
    client_id = _pick("c", "client_id", "clientId", "id")
    refresh_token = _pick("t", "refresh_token", "refreshToken", "token")

    if account or password or client_id or refresh_token:
        total += 1
        raw_rows.append(
            {
                "line_no": "payload",
                "email": account,
                "password": password,
                "client_id": client_id,
                "refresh_token": refresh_token,
            }
        )

    deduped_rows, dedupe_errors = _dedupe_outlook_rows(raw_rows)
    errors.extend(dedupe_errors)
    return total, deduped_rows, errors


def _insert_outlook_rows(
    *,
    parsed_rows: list[dict[str, Any]],
    initial_errors: list[str],
    enabled: bool,
    source: str,
    source_tag: str,
    total: int,
) -> OutlookBatchImportResponse:
    success = 0
    accounts: List[Dict[str, Any]] = []
    errors = list(initial_errors)
    failed = len(errors)

    if not parsed_rows:
        return OutlookBatchImportResponse(
            total=total,
            success=0,
            failed=failed,
            accounts=[],
            errors=errors,
        )

    with Session(engine) as session:
        email_list = [row["email"] for row in parsed_rows]
        existing_items = session.exec(
            select(OutlookAccountModel).where(OutlookAccountModel.email.in_(email_list))
        ).all()
        existing_emails = {str(item.email or "").strip().lower() for item in existing_items}

        rows_to_insert: list[dict[str, Any]] = []
        for row in parsed_rows:
            if row["email"].lower() in existing_emails:
                failed += 1
                errors.append(f"行 {row['line_no']}: 邮箱已存在: {row['email']}")
                if source:
                    _save_reimport_event(
                        session,
                        email=row["email"],
                        source=source,
                        result="duplicate",
                        detail={"line_no": row["line_no"]},
                    )
                continue
            rows_to_insert.append(row)

        now = _utcnow()
        models = [
            OutlookAccountModel(
                email=row["email"],
                password=row["password"],
                client_id=row["client_id"],
                refresh_token=row["refresh_token"],
                enabled=bool(enabled),
                source_tag=source_tag,
                created_at=now,
                updated_at=now,
            )
            for row in rows_to_insert
        ]

        if models:
            try:
                session.add_all(models)
                if source:
                    for row in rows_to_insert:
                        _save_reimport_event(
                            session,
                            email=row["email"],
                            source=source,
                            result="imported",
                            detail={"line_no": row["line_no"]},
                        )
                session.commit()
                for account in models:
                    session.refresh(account)
                    accounts.append(
                        {
                            "id": account.id,
                            "email": account.email,
                            "has_oauth": bool(account.client_id and account.refresh_token),
                            "enabled": account.enabled,
                        }
                    )
                success = len(models)
            except Exception as e:
                session.rollback()
                for row in rows_to_insert:
                    try:
                        account = OutlookAccountModel(
                            email=row["email"],
                            password=row["password"],
                            client_id=row["client_id"],
                            refresh_token=row["refresh_token"],
                            enabled=bool(enabled),
                            source_tag=source_tag,
                            created_at=_utcnow(),
                            updated_at=_utcnow(),
                        )
                        session.add(account)
                        session.commit()
                        session.refresh(account)
                        accounts.append(
                            {
                                "id": account.id,
                                "email": account.email,
                                "has_oauth": bool(account.client_id and account.refresh_token),
                                "enabled": account.enabled,
                            }
                        )
                        success += 1
                    except Exception as inner_e:
                        session.rollback()
                        failed += 1
                        errors.append(f"行 {row['line_no']}: 创建失败: {str(inner_e or e)}")
                        if source:
                            with Session(engine) as event_session:
                                _save_reimport_event(
                                    event_session,
                                    email=row["email"],
                                    source=source,
                                    result="failed",
                                    detail={
                                        "line_no": row["line_no"],
                                        "error": str(inner_e or e),
                                    },
                                )
                                event_session.commit()

        if source and not models:
            session.commit()

    return OutlookBatchImportResponse(
        total=total,
        success=success,
        failed=failed,
        accounts=accounts,
        errors=errors,
    )


def _save_reimport_event(
    session: Session,
    *,
    email: str,
    source: str,
    result: str,
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(
        FailedEmailReimportEventModel(
            email=str(email or "").strip(),
            source=str(source or "").strip(),
            result=str(result or "").strip(),
            detail_json=json.dumps(detail or {}, ensure_ascii=False),
        )
    )


@router.post("/batch-import", response_model=OutlookBatchImportResponse)
def batch_import_outlook(request: OutlookBatchImportRequest):
    """
    批量导入 Outlook 邮箱账户

    支持两种格式（每行一个账户，字段用 ---- 分隔）：
    - 邮箱----密码
    - 邮箱----密码----client_id----refresh_token
    """
    total, parsed_rows, errors = _parse_outlook_import_rows(request.data or "")
    source = str(request.source or "").strip()
    source_tag = str(request.source_tag or "").strip() or (
        "failed_reimport" if source in {"failed_email_pool", "failed_email_retry_page"} else "manual"
    )
    return _insert_outlook_rows(
        parsed_rows=parsed_rows,
        initial_errors=errors,
        enabled=bool(request.enabled),
        source=source,
        source_tag=source_tag,
        total=total,
    )


@router.post("/upload-register-machine", response_model=OutlookBatchImportResponse)
def upload_register_machine_outlook(
    payload: dict[str, Any] = Body(default={}),
):
    """
    兼容邮箱注册机上传面板的 Outlook/Hotmail 账号导入接口。

    支持两种 JSON 模板：
    1. {"data": "email----password----client_id----refresh_token"}
    2. {"a":"email","p":"password","c":"client_id","t":"refresh_token"}

    其中 password/client_id/refresh_token 可按需要省略。
    """
    total, parsed_rows, errors = _parse_register_machine_upload_payload(payload)
    return _insert_outlook_rows(
        parsed_rows=parsed_rows,
        initial_errors=errors,
        enabled=True,
        source="register_machine_upload",
        source_tag="register_machine",
        total=total,
    )


@router.get("", response_model=OutlookListResponse)
def list_outlook_accounts(
    q: str = "",
    enabled: str = "",
    source_tag: str = "",
    page: int = 1,
    page_size: int = 50,
):
    query = select(OutlookAccountModel)

    keyword = str(q or "").strip()
    if keyword:
        query = query.where(OutlookAccountModel.email.ilike(f"%{keyword}%"))

    enabled_raw = str(enabled or "").strip().lower()
    source_tag_raw = str(source_tag or "").strip().lower()
    if enabled_raw in {"true", "1", "yes"}:
        query = query.where(OutlookAccountModel.enabled == True)  # noqa: E712
    elif enabled_raw in {"false", "0", "no"}:
        query = query.where(OutlookAccountModel.enabled == False)  # noqa: E712
    if source_tag_raw:
        query = query.where(OutlookAccountModel.source_tag == source_tag_raw)

    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 50), 10), 200)
    offset = (page - 1) * page_size

    with Session(engine) as session:
        count_query = select(func.count()).select_from(OutlookAccountModel)
        if keyword:
            count_query = count_query.where(OutlookAccountModel.email.ilike(f"%{keyword}%"))
        if enabled_raw in {"true", "1", "yes"}:
            count_query = count_query.where(OutlookAccountModel.enabled == True)  # noqa: E712
        elif enabled_raw in {"false", "0", "no"}:
            count_query = count_query.where(OutlookAccountModel.enabled == False)  # noqa: E712
        if source_tag_raw:
            count_query = count_query.where(OutlookAccountModel.source_tag == source_tag_raw)

        total = int(session.exec(count_query).one() or 0)
        items = session.exec(
            query.order_by(OutlookAccountModel.id.desc()).offset(offset).limit(page_size)
        ).all()

    return OutlookListResponse(
        total=total,
        items=[
            OutlookAccountItem(
                id=int(item.id or 0),
                email=item.email,
                enabled=bool(item.enabled),
                has_oauth=bool(item.client_id and item.refresh_token),
                source_tag=str(item.source_tag or "manual"),
                created_at=item.created_at,
                updated_at=item.updated_at,
                last_used=item.last_used,
            )
            for item in items
        ],
    )


@router.get("/export")
def export_outlook_accounts(
    enabled: str = "",
):
    query = select(OutlookAccountModel)
    enabled_raw = str(enabled or "").strip().lower()
    if enabled_raw in {"true", "1", "yes"}:
        query = query.where(OutlookAccountModel.enabled == True)  # noqa: E712
    elif enabled_raw in {"false", "0", "no"}:
        query = query.where(OutlookAccountModel.enabled == False)  # noqa: E712

    with Session(engine) as session:
        accounts = session.exec(query.order_by(OutlookAccountModel.id.asc())).all()

    output = io.StringIO()
    for acc in accounts:
        if acc.client_id and acc.refresh_token:
            output.write(f"{acc.email}----{acc.password}----{acc.client_id}----{acc.refresh_token}\n")
        else:
            output.write(f"{acc.email}----{acc.password}\n")
    data = output.getvalue().encode("utf-8")

    return StreamingResponse(
        iter([data]),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=outlook_accounts.txt",
            "Cache-Control": "no-store",
        },
    )


@router.post("/check-health")
def check_outlook_accounts_health(request: OutlookHealthCheckRequest):
    from core.base_mailbox import create_mailbox

    ids = [int(x) for x in (request.ids or []) if int(x) > 0]
    q = str(request.q or "").strip()
    enabled_raw = str(request.enabled or "").strip().lower()
    source_tag_raw = str(request.source_tag or "").strip().lower()
    disable_invalid = bool(request.disable_invalid)
    limit = min(max(int(request.limit or 200), 1), 1000)

    query = select(OutlookAccountModel)
    if ids:
        query = query.where(OutlookAccountModel.id.in_(ids))
    if q:
        query = query.where(OutlookAccountModel.email.ilike(f"%{q}%"))
    if enabled_raw in {"true", "1", "yes"}:
        query = query.where(OutlookAccountModel.enabled == True)  # noqa: E712
    elif enabled_raw in {"false", "0", "no"}:
        query = query.where(OutlookAccountModel.enabled == False)  # noqa: E712
    if source_tag_raw:
        query = query.where(OutlookAccountModel.source_tag == source_tag_raw)

    mailbox = create_mailbox(provider="outlook", extra={}, proxy=None)
    results: list[dict[str, Any]] = []
    checked = 0
    healthy = 0
    invalid = 0
    disabled = 0

    with Session(engine) as session:
        rows = session.exec(
            query.order_by(OutlookAccountModel.id.asc()).limit(limit)
        ).all()

        for row in rows:
            payload = {
                "id": row.id,
                "email": row.email,
                "password": row.password,
                "client_id": row.client_id,
                "refresh_token": row.refresh_token,
                "source_tag": row.source_tag,
            }
            valid, reason = mailbox._validate_claimed_account(payload)  # type: ignore[attr-defined]
            checked += 1
            if valid:
                healthy += 1
            else:
                invalid += 1
                if disable_invalid and row.enabled:
                    row.enabled = False
                    row.updated_at = _utcnow()
                    session.add(row)
                    disabled += 1
            results.append(
                {
                    "id": int(row.id or 0),
                    "email": row.email,
                    "valid": bool(valid),
                    "reason": str(reason or ""),
                    "enabled": bool(row.enabled),
                    "source_tag": str(row.source_tag or "manual"),
                }
            )

        session.commit()

    return {
        "checked": checked,
        "healthy": healthy,
        "invalid": invalid,
        "disabled": disabled,
        "items": results,
    }


def _run_outlook_health_check_task(task_id: str, request: OutlookHealthCheckRequest) -> None:
    from core.base_mailbox import create_mailbox
    from api.tasks import _task_store

    _task_store.mark_running(task_id)
    ids = [int(x) for x in (request.ids or []) if int(x) > 0]
    q = str(request.q or "").strip()
    enabled_raw = str(request.enabled or "").strip().lower()
    source_tag_raw = str(request.source_tag or "").strip().lower()
    disable_invalid = bool(request.disable_invalid)
    limit = min(max(int(request.limit or 200), 1), 1000)

    query = select(OutlookAccountModel)
    if ids:
        query = query.where(OutlookAccountModel.id.in_(ids))
    if q:
        query = query.where(OutlookAccountModel.email.ilike(f"%{q}%"))
    if enabled_raw in {"true", "1", "yes"}:
        query = query.where(OutlookAccountModel.enabled == True)  # noqa: E712
    elif enabled_raw in {"false", "0", "no"}:
        query = query.where(OutlookAccountModel.enabled == False)  # noqa: E712
    if source_tag_raw:
        query = query.where(OutlookAccountModel.source_tag == source_tag_raw)

    mailbox = create_mailbox(provider="outlook", extra={}, proxy=None)
    success = 0
    skipped = 0
    errors: list[str] = []

    with Session(engine) as session:
        rows = session.exec(query.order_by(OutlookAccountModel.id.asc()).limit(limit)).all()
        total = len(rows)
        _task_store.set_progress(task_id, f"0/{total}")

        task_rows = [
            {
                "id": int(row.id or 0),
                "email": str(row.email or "").strip(),
                "password": row.password,
                "client_id": row.client_id,
                "refresh_token": row.refresh_token,
                "source_tag": row.source_tag,
                "enabled": bool(row.enabled),
            }
            for row in rows
        ]

    def _worker(payload: dict[str, Any]) -> dict[str, Any]:
        local_mailbox = create_mailbox(provider="outlook", extra={}, proxy=None)
        valid, reason = _retry_validate_outlook_account(local_mailbox, payload, retries=3)
        return {
            "id": int(payload.get("id") or 0),
            "email": str(payload.get("email") or "").strip(),
            "valid": bool(valid),
            "reason": str(reason or ""),
            "enabled": bool(payload.get("enabled")),
        }

    completed = 0
    max_workers = min(10, max(1, len(task_rows)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {pool.submit(_worker, payload): payload for payload in task_rows}
        for future in as_completed(future_map):
            payload = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {
                    "id": int(payload.get("id") or 0),
                    "email": str(payload.get("email") or "").strip(),
                    "valid": False,
                    "reason": str(exc),
                    "enabled": bool(payload.get("enabled")),
                }

            email = str(result.get("email") or "").strip()
            valid = bool(result.get("valid"))
            reason = str(result.get("reason") or "")
            if valid:
                success += 1
                _task_log(task_id, f"[OK] {email} 可用 ({reason})")
            else:
                errors.append(f"{email}: {reason}")
                _task_log(task_id, f"[FAIL] {email} 异常 ({reason})")
                if disable_invalid and bool(result.get("enabled")):
                    with Session(engine) as session:
                        db_row = session.get(OutlookAccountModel, int(result.get("id") or 0))
                        if db_row and db_row.enabled:
                            db_row.enabled = False
                            db_row.updated_at = _utcnow()
                            session.add(db_row)
                            session.commit()
                            skipped += 1
                            _task_log(task_id, f"[SKIP] 已禁用异常邮箱: {email}")
            completed += 1
            _task_store.set_progress(task_id, f"{completed}/{total}")

    _finish_async_task(task_id, success=success, skipped=skipped, errors=errors)


@router.post("/check-health/start")
def start_outlook_health_check_task(request: OutlookHealthCheckRequest, background_tasks: BackgroundTasks):
    with Session(engine) as session:
        query = select(func.count()).select_from(OutlookAccountModel)
        ids = [int(x) for x in (request.ids or []) if int(x) > 0]
        q = str(request.q or "").strip()
        enabled_raw = str(request.enabled or "").strip().lower()
        source_tag_raw = str(request.source_tag or "").strip().lower()
        if ids:
            query = query.where(OutlookAccountModel.id.in_(ids))
        if q:
            query = query.where(OutlookAccountModel.email.ilike(f"%{q}%"))
        if enabled_raw in {"true", "1", "yes"}:
            query = query.where(OutlookAccountModel.enabled == True)  # noqa: E712
        elif enabled_raw in {"false", "0", "no"}:
            query = query.where(OutlookAccountModel.enabled == False)  # noqa: E712
        if source_tag_raw:
            query = query.where(OutlookAccountModel.source_tag == source_tag_raw)
        total = int(session.exec(query).one() or 0)

    task_id = _create_async_task(
        platform="outlook_health_check",
        total=total,
        source="manual_outlook_health_check",
        meta={"request": request.model_dump()},
    )
    background_tasks.add_task(_run_outlook_health_check_task, task_id, request)
    return {"task_id": task_id}


@router.post("/batch-delete")
def batch_delete_outlook_accounts(request: OutlookBatchDeleteRequest):
    ids = [int(x) for x in (request.ids or []) if int(x) > 0]
    if not ids:
        return {"ok": True, "deleted": 0}

    with Session(engine) as session:
        items = session.exec(select(OutlookAccountModel).where(OutlookAccountModel.id.in_(ids))).all()
        for item in items:
            session.delete(item)
        session.commit()
        return {"ok": True, "deleted": len(items)}


@router.patch("/{account_id}")
def update_outlook_account(account_id: int, request: OutlookUpdateRequest):
    account_id = int(account_id)
    with Session(engine) as session:
        account = session.exec(select(OutlookAccountModel).where(OutlookAccountModel.id == account_id)).first()
        if not account:
            return {"ok": False, "error": "not_found"}

        if request.enabled is not None:
            account.enabled = bool(request.enabled)
        if request.password is not None:
            account.password = str(request.password)
        if request.client_id is not None:
            account.client_id = str(request.client_id)
        if request.refresh_token is not None:
            account.refresh_token = str(request.refresh_token)
        account.updated_at = _utcnow()

        session.add(account)
        session.commit()
        session.refresh(account)
        return {
            "ok": True,
            "id": account.id,
            "email": account.email,
            "enabled": account.enabled,
            "has_oauth": bool(account.client_id and account.refresh_token),
        }


@router.delete("/{account_id}")
def delete_outlook_account(account_id: int):
    account_id = int(account_id)
    with Session(engine) as session:
        account = session.exec(select(OutlookAccountModel).where(OutlookAccountModel.id == account_id)).first()
        if not account:
            return {"ok": True, "deleted": 0}
        session.delete(account)
        session.commit()
        return {"ok": True, "deleted": 1}
