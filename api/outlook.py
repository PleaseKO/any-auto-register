from datetime import datetime, timezone
import io
from typing import List, Dict, Any, Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from core.db import engine, OutlookAccountModel

router = APIRouter(prefix="/outlook", tags=["outlook"])


def _utcnow():
    return datetime.now(timezone.utc)


class OutlookBatchImportRequest(BaseModel):
    data: str
    enabled: bool = True


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
    created_at: datetime
    updated_at: datetime
    last_used: Optional[datetime] = None


class OutlookListResponse(BaseModel):
    total: int
    items: List[OutlookAccountItem]


class OutlookBatchDeleteRequest(BaseModel):
    ids: List[int]


class OutlookUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    password: Optional[str] = None
    client_id: Optional[str] = None
    refresh_token: Optional[str] = None


@router.post("/batch-import", response_model=OutlookBatchImportResponse)
def batch_import_outlook(request: OutlookBatchImportRequest):
    """
    批量导入 Outlook 邮箱账户

    支持两种格式（每行一个账户，字段用 ---- 分隔）：
    - 邮箱----密码
    - 邮箱----密码----client_id----refresh_token
    """
    lines = (request.data or "").splitlines()
    total = len(lines)
    success = 0
    failed = 0
    accounts: List[Dict[str, Any]] = []
    errors: List[str] = []

    with Session(engine) as session:
        for idx, raw_line in enumerate(lines):
            line = str(raw_line or "").strip()
            if not line or line.startswith("#"):
                continue

            parts = [part.strip() for part in line.split("----")]
            if len(parts) < 2:
                failed += 1
                errors.append(f"行 {idx + 1}: 格式错误，至少需要邮箱和密码")
                continue

            email = parts[0]
            password = parts[1]
            if "@" not in email:
                failed += 1
                errors.append(f"行 {idx + 1}: 无效的邮箱地址: {email}")
                continue

            existing = session.exec(
                select(OutlookAccountModel).where(OutlookAccountModel.email == email)
            ).first()
            if existing:
                failed += 1
                errors.append(f"行 {idx + 1}: 邮箱已存在: {email}")
                continue

            client_id = parts[2] if len(parts) >= 3 else ""
            refresh_token = parts[3] if len(parts) >= 4 else ""

            try:
                account = OutlookAccountModel(
                    email=email,
                    password=password,
                    client_id=client_id,
                    refresh_token=refresh_token,
                    enabled=bool(request.enabled),
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
            except Exception as e:
                session.rollback()
                failed += 1
                errors.append(f"行 {idx + 1}: 创建失败: {str(e)}")

    return OutlookBatchImportResponse(
        total=total,
        success=success,
        failed=failed,
        accounts=accounts,
        errors=errors,
    )


@router.get("", response_model=OutlookListResponse)
def list_outlook_accounts(
    q: str = "",
    enabled: str = "",
    page: int = 1,
    page_size: int = 50,
):
    query = select(OutlookAccountModel)

    keyword = str(q or "").strip()
    if keyword:
        query = query.where(OutlookAccountModel.email.ilike(f"%{keyword}%"))

    enabled_raw = str(enabled or "").strip().lower()
    if enabled_raw in {"true", "1", "yes"}:
        query = query.where(OutlookAccountModel.enabled == True)  # noqa: E712
    elif enabled_raw in {"false", "0", "no"}:
        query = query.where(OutlookAccountModel.enabled == False)  # noqa: E712

    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 50), 10), 200)
    offset = (page - 1) * page_size

    with Session(engine) as session:
        all_items = session.exec(query.order_by(OutlookAccountModel.id.desc())).all()
        total = len(all_items)
        items = all_items[offset : offset + page_size]

    return OutlookListResponse(
        total=total,
        items=[
            OutlookAccountItem(
                id=int(item.id or 0),
                email=item.email,
                enabled=bool(item.enabled),
                has_oauth=bool(item.client_id and item.refresh_token),
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
        headers={"Content-Disposition": "attachment; filename=outlook_accounts.txt"},
    )


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
