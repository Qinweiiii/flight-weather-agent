"""Shared read-only SQLite boundary for MCP and deterministic skills."""
import json
import re
import sqlite3
import time
from pathlib import Path


def validate_readonly_sql(sql):
    if not isinstance(sql, str) or not sql.strip():
        return {"ok": False, "reason": "empty_sql"}
    text = re.sub(r"/\*.*?\*/|--[^\n]*", " ", sql, flags=re.S).lstrip()
    if not re.match(r"(?:SELECT|WITH)\b", text, re.I):
        return {"ok": False, "reason": "only_select_or_cte_allowed"}
    return {"ok": True, "reason": "sqlite_authorizer_enforced"}


def read_rows(db_path, sql, params=(), max_rows=2000, timeout_seconds=5):
    check = validate_readonly_sql(sql)
    if not check["ok"]:
        raise ValueError(check["reason"])
    if len(sql) > 65536:
        raise ValueError('sql_length_limit_exceeded')
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    conn.row_factory = sqlite3.Row
    deadline = time.monotonic() + timeout_seconds
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}

    def authorize(action, arg1, arg2, database, trigger):
        if action not in allowed:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in {"load_extension", "writefile", "readfile"}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    try:
        conn.execute("PRAGMA query_only=ON")
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
        conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 65536)
        conn.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 100)
        conn.set_authorizer(authorize)
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        rows, size = [], 0
        cursor = conn.execute(sql, params)
        names = [column[0] for column in cursor.description]
        if len(names) != len(set(names)):
            raise ValueError('duplicate_column_names: use distinct aliases')
        for row in cursor:
            if len(rows) >= max_rows:
                raise ValueError(f"result_row_limit_exceeded:{max_rows}; aggregate or add LIMIT")
            if any(isinstance(v, bytes) for v in row):
                raise ValueError('blob_results_not_supported')
            size += sum(len(str(v).encode('utf-8')) for v in row)
            if size > 2_000_000:
                raise ValueError('result_byte_limit_exceeded')
            rows.append(dict(row))
        return rows
    finally:
        conn.close()


def execute_json(db_path, sql):
    try:
        return json.dumps(read_rows(db_path, sql), ensure_ascii=False, allow_nan=False)
    except (sqlite3.Error, ValueError, OSError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
