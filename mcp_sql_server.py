"""Independent read-only SQL executor over MCP stdio."""
import os
from pathlib import Path
import yaml
from mcp.server.fastmcp import FastMCP
from tools.sql_executor import execute_json

mcp = FastMCP("sql-query-server")
ROOT = Path(__file__).resolve().parent


def _load_db_path():
    with (ROOT / "config/config.yaml").open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    raw = os.getenv("FLIGHT_DB_PATH") or config["database"]["path"]
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


DB_CONFIG = {"type": "sqlite", "path": _load_db_path()}


@mcp.tool()
def execute_sql(sql: str) -> str:
    """Execute one SELECT/CTE; return JSON rows, [] for empty, or error."""
    return _execute_sqlite(sql)


def _execute_sqlite(sql: str) -> str:
    return execute_json(DB_CONFIG["path"], sql)


if __name__ == "__main__":
    mcp.run()
