"""
意图枚举定义

与 MasterAgent 中支持的 6 种意图一一对应，
字符串值直接匹配 _route_after_intent 的 key。
"""

from enum import Enum


class Intent(Enum):
    SIMPLE_ANSWER    = "simple_answer"
    SQL_ONLY         = "sql_only"
    ANALYSIS_ONLY    = "analysis_only"
    SQL_AND_ANALYSIS = "sql_and_analysis"
    WEB_SEARCH       = "web_search"
    SEARCH_AND_SQL   = "search_and_sql"
