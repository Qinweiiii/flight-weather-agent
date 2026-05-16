"""
基于FastMCP的SQL查询工具服务器

提供简单的SQL执行功能，方便扩展不同的数据源。
"""

import sqlite3
import json
from pathlib import Path
import yaml

from mcp.server.fastmcp import FastMCP

# 创建FastMCP服务器
mcp = FastMCP("sql-query-server")

def _load_db_path() -> Path:
    """从 config/config.yaml 读取数据库路径，缺失时回退到 flight_weather.db。"""
    project_root = Path(__file__).parent
    config_path = project_root / "config" / "config.yaml"

    default_path = project_root / "data" / "flight_weather.db"
    if not config_path.exists():
        return default_path

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        db_path_raw = (cfg.get("database") or {}).get("path", "./data/flight_weather.db")
        db_path = Path(db_path_raw)
        if not db_path.is_absolute():
            db_path = (project_root / db_path).resolve()
        return db_path
    except Exception:
        return default_path


# 数据库配置
DB_CONFIG = {
    "type": "sqlite",
    "path": _load_db_path()
}


@mcp.tool()
def execute_sql(sql: str) -> str:
    """执行SQL查询
    
    Args:
        sql: SQL查询语句
        db_type: 数据库类型（sqlite）
        
    Returns:
        JSON格式的查询结果
    """
    return _execute_sqlite(sql)


def _execute_sqlite(sql: str) -> str:
    """执行SQLite查询"""
    db_path = DB_CONFIG["path"]
    
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute(sql)
        rows = cursor.fetchall()
        
        if rows:
            result = [dict(row) for row in rows]
            output = json.dumps(result, ensure_ascii=False, indent=2)
        else:
            output = json.dumps({"message": "查询结果为空"})
        
        conn.close()
        return output
        
    except sqlite3.Error as e:
        error_output = json.dumps({
            "error": f"SQL执行错误: {str(e)}",
            "sql": sql
        }, ensure_ascii=False)
        return error_output
    except Exception as e:
        error_output = json.dumps({
            "error": f"未知错误: {str(e)}"
        }, ensure_ascii=False)
        return error_output

if __name__ == "__main__":
    mcp.run()

