from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select, func
from pydantic import BaseModel
from core.db import AccountModel, OutlookAccountModel, TaskLog, get_session
from typing import Optional
from datetime import datetime, timezone
import io, csv, json, logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/accounts", tags=["accounts"])


class AccountCreate(BaseModel):
    platform: str
    email: str
    password: str
    status: str = "registered"
    token: str = ""
    cashier_url: str = ""


class AccountUpdate(BaseModel):
    status: Optional[str] = None
    token: Optional[str] = None
    cashier_url: Optional[str] = None


class ImportRequest(BaseModel):
    platform: str
    lines: list[str]


def _normalize_import_line(line: str) -> tuple[str, str, str] | None:
    parts = str(line or "").strip().split(maxsplit=2)
    if len(parts) < 2:
        return None
    email, password = parts[0].strip(), parts[1].strip()
    if not email or not password:
        return None
    extra = parts[2].strip() if len(parts) > 2 else ""
    if extra:
        try:
            json.loads(extra)
        except (json.JSONDecodeError, ValueError):
            extra = "{}"
    else:
        extra = "{}"
    return email, password, extra


class BatchDeleteRequest(BaseModel):
    ids: list[int]


@router.get("")
def list_accounts(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    email: Optional[str] = None,
    created_at_start: Optional[datetime] = None,
    created_at_end: Optional[datetime] = None,
    page: int = 1,
    page_size: int = 20,
    session: Session = Depends(get_session),
):
    q = select(AccountModel)
    if platform:
        q = q.where(AccountModel.platform == platform)
    if status:
        q = q.where(AccountModel.status == status)
    if email:
        q = q.where(AccountModel.email.contains(email))
    if created_at_start:
        q = q.where(AccountModel.created_at >= created_at_start)
    if created_at_end:
        q = q.where(AccountModel.created_at <= created_at_end)
    count_q = select(func.count()).select_from(AccountModel)
    if platform:
        count_q = count_q.where(AccountModel.platform == platform)
    if status:
        count_q = count_q.where(AccountModel.status == status)
    if email:
        count_q = count_q.where(AccountModel.email.contains(email))
    total = int(session.exec(count_q).one() or 0)
    items = session.exec(q.offset((page - 1) * page_size).limit(page_size)).all()
    return {"total": total, "page": page, "items": items}


@router.post("")
def create_account(body: AccountCreate, session: Session = Depends(get_session)):
    acc = AccountModel(
        platform=body.platform,
        email=body.email,
        password=body.password,
        status=body.status,
        token=body.token,
        cashier_url=body.cashier_url,
    )
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


@router.get("/stats")
def get_stats(session: Session = Depends(get_session)):
    """统计各平台账号数量和状态分布"""
    total = int(session.exec(select(func.count()).select_from(AccountModel)).one() or 0)
    platform_rows = session.exec(
        select(AccountModel.platform, func.count()).group_by(AccountModel.platform)
    ).all()
    status_rows = session.exec(
        select(AccountModel.status, func.count()).group_by(AccountModel.status)
    ).all()
    task_status_rows = session.exec(
        select(TaskLog.status, func.count()).group_by(TaskLog.status)
    ).all()
    failed_logs = session.exec(select(TaskLog).where(TaskLog.status == "failed")).all()
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
    outlook_oauth = int(
        session.exec(
            select(func.count())
            .select_from(OutlookAccountModel)
            .where(OutlookAccountModel.client_id != "")
            .where(OutlookAccountModel.refresh_token != "")
        ).one()
        or 0
    )

    importable_failed = 0
    inferred_failed = 0
    for log in failed_logs:
        try:
            detail = json.loads(log.detail_json or "{}")
        except Exception:
            detail = {}
        extra = detail.get("extra") or {}
        email_value = str(detail.get("email") or log.email or "").strip()
        password_value = str(detail.get("password") or "").strip()
        if email_value and password_value:
            importable_failed += 1
        if extra.get("backfill_mode"):
            inferred_failed += 1

    return {
        "total": total,
        "by_platform": {str(name): int(count or 0) for name, count in platform_rows},
        "by_status": {str(name): int(count or 0) for name, count in status_rows},
        "task_logs": {
            "total": sum(int(count or 0) for _, count in task_status_rows),
            "by_status": {str(name): int(count or 0) for name, count in task_status_rows},
            "failed_email_pool": {
                "total": len(failed_logs),
                "importable": importable_failed,
                "inferred": inferred_failed,
            },
        },
        "outlook_pool": {
            "total": outlook_total,
            "enabled": outlook_enabled,
            "oauth": outlook_oauth,
        },
    }


@router.get("/export")
def export_accounts(
    platform: Optional[str] = None,
    status: Optional[str] = None,
    session: Session = Depends(get_session),
):
    q = select(AccountModel)
    if platform:
        q = q.where(AccountModel.platform == platform)
    if status:
        q = q.where(AccountModel.status == status)
    accounts = session.exec(q).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["platform", "email", "password", "user_id", "region",
                     "status", "cashier_url", "created_at"])
    for acc in accounts:
        writer.writerow([acc.platform, acc.email, acc.password, acc.user_id,
                         acc.region, acc.status, acc.cashier_url,
                         acc.created_at.strftime("%Y-%m-%d %H:%M:%S")])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=accounts.csv"}
    )


@router.post("/import")
def import_accounts(
    body: ImportRequest,
    session: Session = Depends(get_session),
):
    """批量导入，每行格式: email password [extra]"""
    max_lines_per_request = 500
    if len(body.lines) > max_lines_per_request:
        raise HTTPException(400, f"单次最多导入 {max_lines_per_request} 行，请分批提交")

    normalized_rows: list[tuple[str, str, str]] = []
    invalid = 0
    duplicate_in_request = 0
    seen_emails: set[str] = set()
    for line in body.lines:
        parsed = _normalize_import_line(line)
        if not parsed:
            invalid += 1
            continue
        email, password, extra = parsed
        dedup_key = f"{body.platform}:{email.lower()}"
        if dedup_key in seen_emails:
            duplicate_in_request += 1
            continue
        seen_emails.add(dedup_key)
        normalized_rows.append((email, password, extra))

    if not normalized_rows:
        return {"created": 0, "invalid": invalid, "duplicate_in_request": duplicate_in_request, "duplicate_existing": 0}

    existing_rows = session.exec(
        select(AccountModel.email).where(
            AccountModel.platform == body.platform,
            AccountModel.email.in_([email for email, _, _ in normalized_rows]),
        )
    ).all()
    existing_emails = {str(email or "").lower() for email in existing_rows}

    created = 0
    duplicate_existing = 0
    for email, password, extra in normalized_rows:
        if email.lower() in existing_emails:
            duplicate_existing += 1
            continue
        acc = AccountModel(platform=body.platform, email=email, password=password, extra_json=extra)
        session.add(acc)
        created += 1
    session.commit()
    return {
        "created": created,
        "invalid": invalid,
        "duplicate_in_request": duplicate_in_request,
        "duplicate_existing": duplicate_existing,
    }


@router.post("/batch-delete")
def batch_delete_accounts(
    body: BatchDeleteRequest,
    session: Session = Depends(get_session)
):
    """批量删除账号"""
    if not body.ids:
        raise HTTPException(400, "账号 ID 列表不能为空")
    
    if len(body.ids) > 1000:
        raise HTTPException(400, "单次最多删除 1000 个账号")
    
    deleted_count = 0
    not_found_ids = []
    
    try:
        for account_id in body.ids:
            acc = session.get(AccountModel, account_id)
            if acc:
                session.delete(acc)
                deleted_count += 1
            else:
                not_found_ids.append(account_id)
        
        session.commit()
        logger.info(f"批量删除成功: {deleted_count} 个账号")
        
        return {
            "deleted": deleted_count,
            "not_found": not_found_ids,
            "total_requested": len(body.ids)
        }
    except Exception as e:
        session.rollback()
        logger.exception("批量删除失败")
        raise HTTPException(500, f"批量删除失败: {str(e)}")


@router.post("/check-all")
def check_all_accounts(platform: Optional[str] = None,
                       background_tasks: BackgroundTasks = None):
    from core.scheduler import scheduler
    background_tasks.add_task(scheduler.check_accounts_valid, platform)
    return {"message": "批量检测任务已启动"}


@router.get("/{account_id}")
def get_account(account_id: int, session: Session = Depends(get_session)):
    acc = session.get(AccountModel, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    return acc


@router.patch("/{account_id}")
def update_account(account_id: int, body: AccountUpdate,
                   session: Session = Depends(get_session)):
    acc = session.get(AccountModel, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    if body.status is not None:
        acc.status = body.status
    if body.token is not None:
        acc.token = body.token
    if body.cashier_url is not None:
        acc.cashier_url = body.cashier_url
    acc.updated_at = datetime.now(timezone.utc)
    session.add(acc)
    session.commit()
    session.refresh(acc)
    return acc


@router.delete("/{account_id}")
def delete_account(account_id: int, session: Session = Depends(get_session)):
    acc = session.get(AccountModel, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    session.delete(acc)
    session.commit()
    return {"ok": True}


@router.post("/{account_id}/check")
def check_account(account_id: int, background_tasks: BackgroundTasks,
                  session: Session = Depends(get_session)):
    acc = session.get(AccountModel, account_id)
    if not acc:
        raise HTTPException(404, "账号不存在")
    background_tasks.add_task(_do_check, account_id)
    return {"message": "检测任务已启动"}


def _do_check(account_id: int):
    from core.db import engine
    from sqlmodel import Session
    with Session(engine) as s:
        acc = s.get(AccountModel, account_id)
    if acc:
        from core.base_platform import Account, RegisterConfig
        from core.registry import get
        try:
            PlatformCls = get(acc.platform)
            plugin = PlatformCls(config=RegisterConfig())
            obj = Account(platform=acc.platform, email=acc.email,
                         password=acc.password, user_id=acc.user_id,
                         region=acc.region, token=acc.token,
                         extra=json.loads(acc.extra_json or "{}"))
            valid = plugin.check_valid(obj)
            with Session(engine) as s:
                a = s.get(AccountModel, account_id)
                if a:
                    if a.platform != "chatgpt":
                        a.status = a.status if valid else "invalid"
                    a.updated_at = datetime.now(timezone.utc)
                    s.add(a)
                    s.commit()
        except Exception:
            logger.exception("检测账号 %s 时出错", account_id)
