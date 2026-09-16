"""Thin wrappers around existing Agentic BI capabilities.

These classes are deliberately small. They make capabilities explicit for
interview discussion and future routing, without replacing the current
LangGraph workflow.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


class SQLTool:
    """Read-only SQL tool.

    When an SQLQueryAgent is supplied, `run_question` uses the existing NL2SQL
    and SQL Reflection loop. `run_sql` is deterministic and useful for tests or
    internal skills.
    """

    def __init__(self, db_path: str, sql_agent: Optional[Any] = None):
        self.db_path = db_path
        self.sql_agent = sql_agent

    @staticmethod
    def validate_readonly_sql(sql: str) -> Dict[str, Any]:
        normalized = " ".join((sql or "").strip().lower().split())
        if not normalized:
            return {"ok": False, "reason": "empty_sql"}
        if ";" in normalized[:-1]:
            return {"ok": False, "reason": "multiple_statements_not_allowed"}
        if not (normalized.startswith("select") or normalized.startswith("with")):
            return {"ok": False, "reason": "non_readonly_statement"}
        blocked = [
            " drop ", " delete ", " truncate ", " alter ", " create ", " insert ",
            " update ", " attach ", " detach ", " pragma ", " replace ", " vacuum "
        ]
        padded = f" {normalized} "
        for token in blocked:
            if token in padded:
                return {"ok": False, "reason": f"blocked_token:{token.strip()}"}
        return {"ok": True, "reason": "readonly_sql"}

    def run_question(self, question: str, max_retries: int = 3) -> Dict[str, Any]:
        if not self.sql_agent:
            return {"error": "SQLQueryAgent is required for natural-language SQL mode"}
        return self.sql_agent.query(question, max_retries=max_retries)

    def run_sql(self, sql: str) -> Dict[str, Any]:
        check = self.validate_readonly_sql(sql)
        if not check["ok"]:
            return {"sql": sql, "data": None, "error": f"SQL safety check failed: {check['reason']}"}

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(sql)
            rows = [dict(row) for row in cur.fetchall()]
            return {"sql": sql, "data": json.dumps(rows, ensure_ascii=False), "error": None}
        except Exception as e:
            return {"sql": sql, "data": None, "error": str(e)}
        finally:
            if "conn" in locals():
                conn.close()


class SearchTool:
    """External benchmark search wrapper."""

    def __init__(self, search_agent: Any):
        self.search_agent = search_agent

    def search(self, question: str) -> Dict[str, Any]:
        if not self.search_agent:
            return {"answer": None, "sources": [], "quality": {}, "error": "Search agent is not configured"}
        return self.search_agent.search(question)


class ChartTool:
    """Deterministic ECharts option generator for tabular data."""

    @staticmethod
    def _parse_data(data: Any) -> List[Dict[str, Any]]:
        if isinstance(data, str):
            data = json.loads(data)
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return [row for row in data if isinstance(row, dict)]
        return []

    def generate(self, data: Any, title: str = "") -> Dict[str, Any]:
        rows = self._parse_data(data)
        if len(rows) < 2:
            return {"chart": None, "error": "Need at least two rows to build a chart"}

        keys = list(rows[0].keys())
        x_key = next((k for k in keys if not isinstance(rows[0].get(k), (int, float))), keys[0])
        y_key = next((k for k in keys if isinstance(rows[0].get(k), (int, float))), None)
        if not y_key:
            return {"chart": None, "error": "No numeric field found"}

        chart = {
            "title": {"text": title or y_key},
            "tooltip": {"trigger": "axis"},
            "xAxis": {"type": "category", "data": [str(row.get(x_key, "")) for row in rows[:20]]},
            "yAxis": {"type": "value"},
            "series": [{
                "name": y_key,
                "type": "bar",
                "data": [row.get(y_key, 0) for row in rows[:20]],
            }],
        }
        return {"chart": chart, "error": None}


class _SQLiteSkill:
    def __init__(self, db_path: str):
        self.db_path = db_path

    def _query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
        finally:
            conn.close()

    def _data_period(self) -> Dict[str, Optional[str]]:
        rows = self._query("SELECT MIN(FL_DATE) AS min_date, MAX(FL_DATE) AS max_date FROM flights_enriched")
        return rows[0] if rows else {"min_date": None, "max_date": None}


class AnomalySkill(_SQLiteSkill):
    """Compare a recent period with the previous period for key dimensions."""

    METRICS = {
        "arr_delay_rate": "AVG(COALESCE(ARR_DEL15, 0))",
        "cancel_rate": "AVG(COALESCE(CANCELLED, 0))",
        "avg_arr_delay": "AVG(COALESCE(ARR_DELAY, 0))",
    }
    DIMENSIONS = {"ORIGIN", "DEST", "OP_UNIQUE_CARRIER"}

    def scan(
        self,
        metric: str = "arr_delay_rate",
        dimension: str = "ORIGIN",
        period_days: int = 30,
        min_flights: int = 100,
        limit: int = 10,
    ) -> Dict[str, Any]:
        metric_expr = self.METRICS.get(metric)
        dim = dimension.upper()
        if not metric_expr:
            return {"anomalies": [], "error": f"Unsupported metric: {metric}"}
        if dim not in self.DIMENSIONS:
            return {"anomalies": [], "error": f"Unsupported dimension: {dimension}"}

        sql = f"""
        WITH anchor AS (
            SELECT MAX(FL_DATE) AS max_date FROM flights_enriched
        ),
        bucketed AS (
            SELECT
                {dim} AS dimension_value,
                CASE
                    WHEN FL_DATE >= date((SELECT max_date FROM anchor), '-' || ? || ' day') THEN 'recent'
                    WHEN FL_DATE >= date((SELECT max_date FROM anchor), '-' || (? * 2) || ' day') THEN 'previous'
                END AS bucket,
                {metric_expr} AS metric_value,
                COUNT(*) AS flight_cnt
            FROM flights_enriched
            WHERE FL_DATE >= date((SELECT max_date FROM anchor), '-' || (? * 2) || ' day')
            GROUP BY dimension_value, bucket
            HAVING flight_cnt >= ?
        )
        SELECT
            r.dimension_value,
            r.metric_value AS recent_value,
            p.metric_value AS previous_value,
            r.metric_value - p.metric_value AS delta_value,
            r.flight_cnt AS recent_flights,
            p.flight_cnt AS previous_flights
        FROM bucketed r
        JOIN bucketed p ON r.dimension_value = p.dimension_value
        WHERE r.bucket = 'recent' AND p.bucket = 'previous'
        ORDER BY ABS(delta_value) DESC
        LIMIT ?
        """
        rows = self._query(sql, (period_days, period_days, period_days, min_flights, limit))
        return {
            "anomalies": rows,
            "metric": metric,
            "dimension": dim,
            "period_days": period_days,
            "data_period": self._data_period(),
            "error": None,
        }


class WeatherImpactSkill(_SQLiteSkill):
    """Analyze precipitation exposure versus delay/cancellation outcomes."""

    def analyze(self, precipitation_threshold: float = 2.0, period_days: int = 90) -> Dict[str, Any]:
        sql = """
        WITH anchor AS (
            SELECT MAX(FL_DATE) AS max_date FROM flights_enriched
        )
        SELECT
            CASE
                WHEN COALESCE(prcp_ORIGIN, 0) > ? OR COALESCE(prcp_DEST, 0) > ?
                THEN 'high_precipitation'
                ELSE 'low_precipitation'
            END AS weather_bucket,
            COUNT(*) AS flight_cnt,
            AVG(COALESCE(ARR_DELAY, 0)) AS avg_arr_delay_min,
            AVG(COALESCE(DEP_DELAY, 0)) AS avg_dep_delay_min,
            AVG(COALESCE(ARR_DEL15, 0)) AS arr_delay_rate,
            AVG(COALESCE(CANCELLED, 0)) AS cancel_rate
        FROM flights_enriched
        WHERE FL_DATE >= date((SELECT max_date FROM anchor), '-' || ? || ' day')
        GROUP BY weather_bucket
        ORDER BY weather_bucket
        """
        rows = self._query(sql, (precipitation_threshold, precipitation_threshold, period_days))
        interpretation = "Insufficient bucket data for comparison."
        if len(rows) == 2:
            by_bucket = {row["weather_bucket"]: row for row in rows}
            high = by_bucket.get("high_precipitation")
            low = by_bucket.get("low_precipitation")
            if high and low:
                delay_delta = high["avg_arr_delay_min"] - low["avg_arr_delay_min"]
                cancel_delta = high["cancel_rate"] - low["cancel_rate"]
                interpretation = (
                    f"High precipitation flights show {delay_delta:.2f} more arrival-delay minutes "
                    f"and {cancel_delta:.2%} higher cancellation rate than low precipitation flights."
                )
        return {
            "buckets": rows,
            "precipitation_threshold": precipitation_threshold,
            "period_days": period_days,
            "interpretation": interpretation,
            "data_period": self._data_period(),
            "error": None,
        }


class ReportSkill(_SQLiteSkill):
    """Generate a deterministic operations summary."""

    def generate(self, period: str = "weekly") -> Dict[str, Any]:
        days = 7 if period == "weekly" else 1
        sql = """
        WITH anchor AS (
            SELECT MAX(FL_DATE) AS max_date FROM flights_enriched
        )
        SELECT
            COUNT(*) AS flight_cnt,
            AVG(COALESCE(DEP_DELAY, 0)) AS avg_dep_delay_min,
            AVG(COALESCE(ARR_DELAY, 0)) AS avg_arr_delay_min,
            AVG(COALESCE(DEP_DEL15, 0)) AS dep_delay_rate,
            AVG(COALESCE(ARR_DEL15, 0)) AS arr_delay_rate,
            AVG(COALESCE(CANCELLED, 0)) AS cancel_rate,
            AVG(COALESCE(DIVERTED, 0)) AS divert_rate,
            MIN(FL_DATE) AS period_start,
            MAX(FL_DATE) AS period_end
        FROM flights_enriched
        WHERE FL_DATE >= date((SELECT max_date FROM anchor), '-' || ? || ' day')
        """
        rows = self._query(sql, (days,))
        metrics = rows[0] if rows else {}
        report = (
            f"## {period.title()} Flight Operations Summary\n\n"
            f"- Observation window: {metrics.get('period_start')} to {metrics.get('period_end')}\n"
            f"- Flights: {metrics.get('flight_cnt', 0)}\n"
            f"- Avg departure delay: {metrics.get('avg_dep_delay_min', 0):.2f} min\n"
            f"- Avg arrival delay: {metrics.get('avg_arr_delay_min', 0):.2f} min\n"
            f"- Departure delay rate: {metrics.get('dep_delay_rate', 0):.2%}\n"
            f"- Arrival delay rate: {metrics.get('arr_delay_rate', 0):.2%}\n"
            f"- Cancellation rate: {metrics.get('cancel_rate', 0):.2%}\n"
            f"- Diversion rate: {metrics.get('divert_rate', 0):.2%}\n"
        )
        return {"report": report, "metrics": metrics, "period": period, "error": None}
