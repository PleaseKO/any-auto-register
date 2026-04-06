#!/usr/bin/env python3
"""
把本项目 SQLite 里已注册的账号导入到远端 Sub2API。

只做“新增/更新（由 Sub2API 端决定）”的导入请求，不会删除任何本地/远端数据。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sqlmodel import Session, create_engine, select

from core.db import AccountModel
from platforms.chatgpt import sub2api_upload as s2


DEFAULT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


@dataclass(frozen=True)
class CodexAccount:
    email: str
    access_token: str
    refresh_token: str = ""
    id_token: str = ""
    client_id: str = DEFAULT_CLIENT_ID


def _now_ts() -> int:
    return int(time.time())


def _parse_group_ids(raw: str | None) -> list[int] | None:
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            continue
    return out or None


def _default_db_path() -> Path:
    candidates = [
        Path("./data/account_manager.db"),
        Path("./account_manager.db"),
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return candidates[0]

def _read_config_from_sqlite(db_path: Path, key: str) -> str:
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            cur = conn.execute("SELECT value FROM configs WHERE key=? LIMIT 1", (key,))
            row = cur.fetchone()
            return str(row[0] or "").strip() if row else ""
        finally:
            conn.close()
    except Exception:
        return ""


def _mask_secret(value: str, keep: int = 6) -> str:
    v = str(value or "")
    if len(v) <= keep * 2:
        return "*" * len(v)
    return f"{v[:keep]}...{v[-keep:]}"


def _mk_engine(db_path: Path):
    # absolute path -> sqlite:////abs/path.db
    return create_engine(f"sqlite:///{db_path.resolve()}")


def _to_codex_account(acc: AccountModel) -> CodexAccount | None:
    extra = acc.get_extra()
    access_token = str(extra.get("access_token") or acc.token or "").strip()
    if not access_token:
        return None
    return CodexAccount(
        email=str(acc.email or "").strip(),
        access_token=access_token,
        refresh_token=str(extra.get("refresh_token") or "").strip(),
        id_token=str(extra.get("id_token") or "").strip(),
        client_id=str(extra.get("client_id") or DEFAULT_CLIENT_ID).strip() or DEFAULT_CLIENT_ID,
    )


def _post_sub2api_account(
    *,
    codex_acc: CodexAccount,
    api_url: str,
    api_key: str,
    group_ids: list[int] | None,
    timeout_s: int,
    ignore_duplicates: bool,
    verify_tls: bool,
) -> tuple[bool, int | None, str]:
    payload = s2._build_sub2api_account_payload(codex_acc, group_ids=group_ids)
    url = f"{api_url.rstrip('/')}/api/v1/admin/accounts"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{api_url.rstrip('/')}/admin/accounts",
        "x-api-key": api_key,
    }
    # 注意：在当前沙盒环境下，Python 直连网络可能受限；
    # 这里用 curl 子进程发请求（不会删除任何数据）。
    try:
        args: list[str] = [
            "curl",
            "-sS",
            "--max-time",
            str(int(timeout_s)),
            "-X",
            "POST",
            url,
            "-H",
            "Content-Type: application/json",
            "-H",
            "Accept: application/json, text/plain, */*",
            "-H",
            f"Referer: {api_url.rstrip('/')}/admin/accounts",
            "-H",
            f"x-api-key: {api_key}",
            "--data-binary",
            json.dumps(payload, ensure_ascii=False),
            "-w",
            "\n__CURL_HTTP_CODE__:%{http_code}\n",
        ]
        if not verify_tls:
            args.insert(1, "-k")

        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        out = proc.stdout or ""
        http_code: int | None = None
        body = out
        marker = "__CURL_HTTP_CODE__:"
        if marker in out:
            body, tail = out.rsplit(marker, 1)
            tail = tail.strip()
            try:
                http_code = int(tail.splitlines()[0].strip())
            except Exception:
                http_code = None
        if http_code in (200, 201):
            return True, http_code, "ok"
        if ignore_duplicates and http_code in (409,):
            return True, http_code, "duplicate_ignored"
        msg = (body.strip() or f"http_{http_code}")[:300]
        return False, http_code, msg
    except Exception as exc:
        return False, None, f"exception: {exc}"

def _curl_json(*args: str, timeout_s: int = 10) -> dict[str, Any]:
    cmd = ["curl", "-sS", "--max-time", str(int(timeout_s)), *args]
    raw = subprocess.check_output(cmd, text=True)
    obj = json.loads(raw)
    return obj if isinstance(obj, dict) else {}


def _fetch_remote_names(
    *,
    api_url: str,
    api_key: str,
    page_size: int = 200,
    timeout_s: int = 10,
) -> set[str]:
    base = f"{api_url.rstrip('/')}/api/v1/admin/accounts"
    names: set[str] = set()
    first = _curl_json(
        f"{base}?page=1&page_size={int(page_size)}",
        "-H",
        f"x-api-key: {api_key}",
        timeout_s=timeout_s,
    )
    data = first.get("data") if isinstance(first.get("data"), dict) else {}
    pages = int(data.get("pages") or 1)
    # 兼容某些实现不支持 page_size：以 pages 为准循环
    for p in range(1, pages + 1):
        obj = first if p == 1 else _curl_json(
            f"{base}?page={p}&page_size={int(page_size)}",
            "-H",
            f"x-api-key: {api_key}",
            timeout_s=timeout_s,
        )
        d = obj.get("data") if isinstance(obj.get("data"), dict) else {}
        items = d.get("items") or []
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name") or "").strip()
            if name:
                names.add(name)
    return names


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="从 account_manager.db 导入 ChatGPT 账号到远端 Sub2API（不删除任何数据）"
    )
    parser.add_argument("--db", default=str(_default_db_path()), help="SQLite DB 路径（默认自动探测）")
    parser.add_argument("--platform", default="chatgpt", help="平台过滤（默认 chatgpt）")
    parser.add_argument("--status", default="", help="状态过滤（逗号分隔，例如 registered,trial,subscribed；为空不限制）")
    parser.add_argument("--limit", type=int, default=0, help="最多导入多少条（0=不限制）")
    parser.add_argument("--offset", type=int, default=0, help="跳过多少条（用于分批导入）")
    parser.add_argument("--only-ids", default="", help="仅导入指定 id（逗号分隔）")

    parser.add_argument("--sub2api-url", default=os.getenv("SUB2API_API_URL", ""), required=False)
    parser.add_argument("--sub2api-key", default=os.getenv("SUB2API_API_KEY", ""), required=False)
    parser.add_argument("--group-ids", default=os.getenv("SUB2API_GROUP_IDS", ""), help="Sub2API group_ids（逗号分隔）")

    parser.add_argument("--workers", type=int, default=4, help="并发数（默认 4）")
    parser.add_argument("--timeout", type=int, default=30, help="单请求超时秒（默认 30）")
    parser.add_argument("--ignore-duplicates", action="store_true", help="遇到 409 视为成功继续")
    parser.add_argument("--verify-tls", action="store_true", help="启用 TLS 证书校验（默认关闭以兼容自签）")
    parser.add_argument("--remote-dedup", action="store_true", help="导入前拉取远端 name 列表并跳过已存在的账号（不删除任何数据）")
    parser.add_argument("--remote-page-size", type=int, default=200, help="远端列表分页大小（默认 200）")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要导入的账号，不发请求")
    parser.add_argument(
        "--out",
        default=f"/tmp/sub2api_import_{_now_ts()}.jsonl",
        help="输出 JSONL 日志路径（默认 /tmp/sub2api_import_*.jsonl）",
    )

    args = parser.parse_args(argv)

    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(f"[ERROR] DB 文件不存在: {db_path}", file=sys.stderr)
        return 2

    # Sub2API config:
    # - CLI args/env (SUB2API_API_URL/SUB2API_API_KEY) 优先
    # - 若为空，则回退读取同一个 SQLite 的 configs 表：sub2api_api_url/sub2api_api_key/sub2api_group_ids
    api_url = str(args.sub2api_url or "").strip() or _read_config_from_sqlite(db_path, "sub2api_api_url")
    api_key = str(args.sub2api_key or "").strip() or _read_config_from_sqlite(db_path, "sub2api_api_key")
    if not api_url or not api_key:
        print(
            "[ERROR] 缺少 Sub2API 配置：请设置 --sub2api-url/--sub2api-key（或环境变量 SUB2API_API_URL/SUB2API_API_KEY），"
            "或在 SQLite configs 表写入 sub2api_api_url/sub2api_api_key。",
            file=sys.stderr,
        )
        return 2

    group_ids = _parse_group_ids(args.group_ids) or _parse_group_ids(_read_config_from_sqlite(db_path, "sub2api_group_ids"))

    only_ids: set[int] | None = None
    if str(args.only_ids or "").strip():
        only_ids = set()
        for part in str(args.only_ids).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                only_ids.add(int(part))
            except ValueError:
                continue
        if not only_ids:
            only_ids = None

    statuses: set[str] | None = None
    if str(args.status or "").strip():
        statuses = {s.strip() for s in str(args.status).split(",") if s.strip()}
        if not statuses:
            statuses = None

    engine = _mk_engine(db_path)

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    rows: list[AccountModel] = []
    with Session(engine) as session:
        stmt = select(AccountModel).where(AccountModel.platform == args.platform)
        if only_ids:
            stmt = stmt.where(AccountModel.id.in_(sorted(only_ids)))
        if statuses:
            stmt = stmt.where(AccountModel.status.in_(sorted(statuses)))
        stmt = stmt.order_by(AccountModel.id)
        if args.offset and args.offset > 0:
            stmt = stmt.offset(int(args.offset))
        if args.limit and args.limit > 0:
            stmt = stmt.limit(int(args.limit))
        rows = list(session.exec(stmt).all())

    if not rows:
        print("[INFO] 没有匹配到可导入的账号。")
        return 0

    print(f"[INFO] 匹配账号数: {len(rows)}")
    print(f"[INFO] Sub2API: {api_url.rstrip('/')}")
    print(f"[INFO] Sub2API Key: {_mask_secret(api_key)}")
    if group_ids:
        print(f"[INFO] group_ids: {group_ids}")
    print(f"[INFO] 输出日志: {out_path}")

    # Prepare tasks
    tasks: list[tuple[int, str, CodexAccount]] = []
    skipped = 0
    for acc in rows:
        codex_acc = _to_codex_account(acc)
        if not codex_acc or not codex_acc.email:
            skipped += 1
            continue
        tasks.append((int(acc.id or 0), codex_acc.email, codex_acc))

    if skipped:
        print(f"[INFO] 跳过无 token/邮箱的账号: {skipped}")

    if args.remote_dedup and tasks:
        try:
            remote_names = _fetch_remote_names(
                api_url=api_url,
                api_key=api_key,
                page_size=int(args.remote_page_size),
                timeout_s=10,
            )
            before = len(tasks)
            tasks = [t for t in tasks if t[1] not in remote_names]
            print(f"[INFO] 远端去重: remote_names={len(remote_names)} skipped={before - len(tasks)} remaining={len(tasks)}")
        except Exception as exc:
            print(f"[WARN] 远端去重失败（将继续直接导入，不会删除数据）: {exc}", file=sys.stderr)

    if args.dry_run:
        for account_id, email, _ in tasks[:50]:
            print(f"[DRY] id={account_id} email={email}")
        if len(tasks) > 50:
            print(f"[DRY] ... 还有 {len(tasks) - 50} 条未展示")
        return 0

    ok_count = 0
    fail_count = 0

    def _job(item: tuple[int, str, CodexAccount]) -> dict[str, Any]:
        account_id, email, codex_acc = item
        ok, status, msg = _post_sub2api_account(
            codex_acc=codex_acc,
            api_url=api_url,
            api_key=api_key,
            group_ids=group_ids,
            timeout_s=int(args.timeout),
            ignore_duplicates=bool(args.ignore_duplicates),
            verify_tls=bool(args.verify_tls),
        )
        return {
            "ts": _now_ts(),
            "id": account_id,
            "email": email,
            "ok": bool(ok),
            "http_status": status,
            "message": msg,
        }

    with out_path.open("a", encoding="utf-8") as fp:
        with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as ex:
            futures = [ex.submit(_job, t) for t in tasks]
            for fut in as_completed(futures):
                row = fut.result()
                fp.write(json.dumps(row, ensure_ascii=False) + "\n")
                fp.flush()
                if row["ok"]:
                    ok_count += 1
                else:
                    fail_count += 1

    print(f"[DONE] success={ok_count} failed={fail_count} total={len(tasks)}")
    if fail_count:
        print(f"[WARN] 失败详情见: {out_path}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
