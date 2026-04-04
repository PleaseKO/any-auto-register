#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlmodel import Session, select

from core.db import OutlookAccountModel, TaskLog, engine


def parse_outlook_file(path: Path) -> list[dict]:
    rows: list[dict] = []
    for idx, raw in enumerate(
        path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1
    ):
        line = str(raw or "").strip()
        if not line:
            continue
        parts = [x.strip() for x in line.split("----")]
        if len(parts) < 2:
            continue
        rows.append(
            {
                "line_no": idx,
                "email": parts[0],
                "password": parts[1],
                "client_id": parts[2] if len(parts) > 2 else "",
                "refresh_token": parts[3] if len(parts) > 3 else "",
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="根据 Outlook 导入文件，对历史失败日志做推断补录"
    )
    parser.add_argument("file", help="Outlook 导入文件路径")
    parser.add_argument("--apply", action="store_true", help="实际写入数据库")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多补录多少条；0 表示不限制",
    )
    args = parser.parse_args()

    source_path = Path(args.file)
    if not source_path.exists():
        raise SystemExit(f"文件不存在: {source_path}")

    file_rows = parse_outlook_file(source_path)
    file_email_map = {row["email"]: row for row in file_rows}

    with Session(engine) as session:
        current_outlook_emails = {
            item.email for item in session.exec(select(OutlookAccountModel)).all()
        }
        known_logged_emails = {
            str(item.email or "").strip()
            for item in session.exec(select(TaskLog)).all()
            if str(item.email or "").strip()
        }
        missing_rows = [
            row
            for row in file_rows
            if row["email"] not in current_outlook_emails
            and row["email"] not in known_logged_emails
        ]

        failed_logs = session.exec(
            select(TaskLog)
            .where(TaskLog.status == "failed")
            .order_by(TaskLog.id.asc())
        ).all()

        empty_failed_logs = []
        exact_matched = 0
        for log in failed_logs:
            try:
                detail = json.loads(log.detail_json or "{}")
            except Exception:
                detail = {}
            log_email = str(detail.get("email") or log.email or "").strip()
            if log_email and log_email in file_email_map:
                exact_matched += 1
                continue
            if not log_email:
                empty_failed_logs.append((log, detail))

        candidates = empty_failed_logs
        missing_candidates = missing_rows
        if args.limit and args.limit > 0:
            candidates = candidates[: args.limit]
            missing_candidates = missing_candidates[: args.limit]

        pair_count = min(len(candidates), len(missing_candidates))

        print(f"文件总条数: {len(file_rows)}")
        print(f"当前 Outlook 池条数: {len(current_outlook_emails)}")
        print(f"文件中已不在 Outlook 池且未出现在任务日志邮箱字段的条数: {len(missing_rows)}")
        print(f"失败日志总数: {len(failed_logs)}")
        print(f"失败日志中已有精确邮箱且能在文件命中的条数: {exact_matched}")
        print(f"失败日志中邮箱为空的条数: {len(empty_failed_logs)}")
        print(f"本次可做推断补录的条数: {pair_count}")
        print("")

        preview = []
        for i in range(pair_count):
            log, _detail = candidates[i]
            row = missing_candidates[i]
            preview.append(
                {
                    "task_log_id": log.id,
                    "created_at": str(log.created_at),
                    "error": log.error,
                    "line_no": row["line_no"],
                    "email": row["email"],
                }
            )

        print("预览前 20 条:")
        for item in preview[:20]:
            print(
                f"  log#{item['task_log_id']} <- line#{item['line_no']} "
                f"{item['email']} | {item['created_at']} | {item['error'][:50]}"
            )

        if not args.apply:
            print("\n当前为 dry-run，未写入数据库。")
            return 0

        updated = 0
        for i in range(pair_count):
            log, detail = candidates[i]
            row = missing_candidates[i]
            detail["email"] = row["email"]
            detail["password"] = row["password"]
            extra = detail.get("extra") or {}
            if row["client_id"]:
                extra["client_id"] = row["client_id"]
            if row["refresh_token"]:
                extra["refresh_token"] = row["refresh_token"]
            extra["backfill_source"] = str(source_path.name)
            extra["backfill_mode"] = "inferred_by_outlook_file_minus_pool"
            extra["backfill_line_no"] = row["line_no"]
            detail["extra"] = extra
            log.email = row["email"]
            log.detail_json = json.dumps(detail, ensure_ascii=False)
            session.add(log)
            updated += 1

        session.commit()
        print(f"\n已写入 {updated} 条推断补录记录。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
