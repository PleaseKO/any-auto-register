from __future__ import annotations

import json
from typing import Any


def parse_task_log_detail(item) -> dict[str, Any]:
    try:
        detail = json.loads(getattr(item, "detail_json", "") or "{}")
        return detail if isinstance(detail, dict) else {}
    except Exception:
        return {}


def summarize_error_reason(text: str) -> str:
    raw = str(text or "").strip()
    lower = raw.lower()
    if not raw:
        return "未记录错误信息"
    if "add_phone" in lower:
        return "命中 add_phone / 手机号验证"
    if "about_you" in lower:
        return "about_you 提交失败"
    if "workspace" in lower or "callback" in lower:
        return "workspace / callback 恢复失败"
    if "验证码" in raw or "otp" in lower:
        return "验证码阶段失败"
    if "proxy" in lower or "代理" in raw:
        return "代理异常"
    if "访问首页失败" in raw:
        return "首页访问失败"
    return "其他失败"


def task_log_import_record(item) -> dict[str, str]:
    detail = parse_task_log_detail(item)
    extra = detail.get("extra") or {}
    email = str(detail.get("email") or getattr(item, "email", "") or "").strip()
    password = str(detail.get("password") or "").strip()
    client_id = str(
        extra.get("client_id") or extra.get("clientId") or extra.get("clientID") or ""
    ).strip()
    refresh_token = str(
        extra.get("refresh_token") or extra.get("refreshToken") or ""
    ).strip()
    return {
        "email": email,
        "password": password,
        "client_id": client_id,
        "refresh_token": refresh_token,
    }


def build_failed_email_row(item) -> dict[str, Any]:
    detail = parse_task_log_detail(item)
    extra = detail.get("extra") or {}
    record = task_log_import_record(item)
    source_mode = str(extra.get("backfill_mode") or "").strip()
    source_label = "推断补录" if source_mode else "原始失败"
    return {
        "id": int(getattr(item, "id", 0) or 0),
        "platform": str(getattr(item, "platform", "") or detail.get("platform") or "").strip(),
        "email": record["email"],
        "password": record["password"],
        "client_id": record["client_id"],
        "refresh_token": record["refresh_token"],
        "has_oauth": bool(record["client_id"] and record["refresh_token"]),
        "importable": bool(record["email"] and record["password"]),
        "error": str(getattr(item, "error", "") or "").strip(),
        "reason": summarize_error_reason(getattr(item, "error", "") or ""),
        "created_at": getattr(item, "created_at", None),
        "source_mode": source_mode,
        "source_label": source_label,
        "backfill_source": str(extra.get("backfill_source") or "").strip(),
        "backfill_line_no": extra.get("backfill_line_no"),
    }
