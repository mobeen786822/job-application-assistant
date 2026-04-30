#!/usr/bin/env python3
"""Validate the Supabase tables required by the Flask app.

Reads SUPABASE_URL and SUPABASE_SERVICE_KEY from the environment or local .env.
This uses PostgREST via supabase-py, so it cannot apply migrations; run
supabase/apply_schema.sql in Supabase SQL Editor if a table/column is missing.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def main() -> int:
    load_env_file(ROOT / ".env")

    url = (os.environ.get("SUPABASE_URL") or "").strip()
    service_key = (os.environ.get("SUPABASE_SERVICE_KEY") or "").strip()
    if not url or not service_key:
        print("Missing SUPABASE_URL or SUPABASE_SERVICE_KEY.", file=sys.stderr)
        return 2

    try:
        from supabase import create_client
    except ImportError as exc:
        print(f"supabase package is not installed: {exc}", file=sys.stderr)
        return 2

    client = create_client(url, service_key)
    checks = {
        "generations": "id,user_id,created_at,job_title,detected_role_type,status",
        "job_leads": (
            "id,user_id,job_hash,created_at,updated_at,title,company,location,url,platform,"
            "description,score,recommendation,detected_role_type,status,matched_terms,"
            "missing_terms,positive_signals,risk_signals,preference_signals,reasons,"
            "generated_resume_html,generated_resume_pdf,generated_cover_letter,generated_cover_pdf"
        ),
    }

    ok = True
    for table, columns in checks.items():
        try:
            resp = client.table(table).select(columns, count="exact").limit(1).execute()
            print(f"OK {table}: {resp.count if resp.count is not None else 'unknown'} row(s)")
        except Exception as exc:  # supabase-py raises APIError with useful text
            ok = False
            print(f"FAIL {table}: {exc}", file=sys.stderr)

    if not ok:
        print("\nRun supabase/apply_schema.sql in the Supabase SQL Editor, then rerun this check.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
