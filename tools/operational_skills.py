"""Small, bounded operational skills sharing one metric contract."""
import json
import math
from tools.sql_executor import read_rows, execute_json, validate_readonly_sql
from tools.metrics import ARR_VALID, METRICS, KPI_SELECT, window, scope_clause, fmt


class SQLTool:
    validate_readonly_sql = staticmethod(validate_readonly_sql)

    def __init__(self, db_path, sql_agent=None):
        self.db_path, self.sql_agent = db_path, sql_agent

    def run_question(self, question, max_retries=3):
        if self.sql_agent is None:
            raise ValueError("SQLQueryAgent is required")
        return self.sql_agent.query(question, max_retries=max_retries)

    def run_sql(self, sql):
        data = execute_json(self.db_path, sql)
        payload = json.loads(data)
        error = payload.get('error') if isinstance(payload, dict) else None
        return {"sql": sql, "data": None if error else data, "error": error}


class SearchTool:
    def __init__(self, search_agent):
        self.search_agent = search_agent

    def search(self, question):
        return self.search_agent.search(question)


class ChartTool:
    def generate(self, data, title="", x_key=None, y_key=None):
        rows = json.loads(data) if isinstance(data, str) else data
        if not isinstance(rows, list) or len(rows) < 2:
            return {"chart": None, "error": "Need at least two rows"}
        keys = list(rows[0])
        x_key = x_key or next((k for k in keys if isinstance(rows[0][k], str)), keys[0])
        measures = [k for k in keys if k != x_key and isinstance(rows[0][k], (int, float))]
        y_key = y_key or next((k for k in measures if any(s in k.lower() for s in ('rate', 'metric', 'delta', 'delay', 'minutes'))), measures[0] if measures else None)
        if not y_key:
            return {"chart": None, "error": "No numeric measure"}
        horizontal = any(len(str(r.get(x_key, ''))) > 10 for r in rows[:20])
        categories = {"type": "category", "data": [str(r.get(x_key, '')) for r in rows[:20]],
                      "axisLabel": {"interval": 0, "width": 125, "overflow": "truncate"}}
        scale = 1000 if y_key=='minutes' and max((abs(r.get(y_key) or 0) for r in rows[:20]), default=0)>=10000 else 1
        unit = ('千分钟' if scale==1000 else '分钟') if y_key=='minutes' else y_key
        values = {"type": "value", "splitNumber": 3, "axisLabel": {"hideOverlap": True}}
        if horizontal:
            categories['inverse'] = True
        return {"chart": {"title": {"text": title or y_key, "subtext": f'单位：{unit}', "textStyle": {"fontSize": 14}},
                "grid": {"left": 12, "right": 25, "top": 55, "bottom": 20, "containLabel": True},
                "tooltip": {"trigger": "axis", "renderMode": "richText"},
                "xAxis": values if horizontal else categories,
                "yAxis": categories if horizontal else values,
                "series": [{"name": unit, "type": "bar", "data": [r[y_key]/scale if isinstance(r.get(y_key),(int,float)) else None for r in rows[:20]]}]}, "error": None}


class _SQLiteSkill:
    def __init__(self, db_path):
        self.db_path = db_path

    def _query(self, sql, params=()):
        return read_rows(self.db_path, sql, params)


class AnomalySkill(_SQLiteSkill):
    METRICS = METRICS
    DIMENSIONS = {"ORIGIN", "DEST", "OP_UNIQUE_CARRIER"}

    def scan(self, metric="arr_delay_rate", dimension="ORIGIN", period_days=30, min_flights=30, limit=10):
        if metric not in METRICS or dimension not in self.DIMENSIONS:
            raise ValueError("Unsupported metric or dimension")
        if not 1 <= min_flights or not 1 <= limit <= 100:
            raise ValueError("Invalid sample threshold or limit")
        w = window(self.db_path, period_days)
        denom = f"SUM(CASE WHEN {ARR_VALID} THEN 1 ELSE 0 END)" if metric != "cancel_rate" else "SUM(CASE WHEN CANCELLED IN (0,1) THEN 1 ELSE 0 END)"
        sql = f"""WITH buckets AS (
          SELECT {dimension} AS dimension_value,
            CASE WHEN FL_DATE >= ? THEN 'recent' ELSE 'previous' END AS bucket,
            {METRICS[metric]} AS metric_value, COUNT(*) AS flight_cnt, {denom} AS observed
          FROM flights_enriched WHERE FL_DATE BETWEEN ? AND ?
          GROUP BY 1,2 HAVING observed >= ?)
          SELECT r.dimension_value, r.metric_value AS recent_value, p.metric_value AS previous_value,
            r.metric_value-p.metric_value AS delta_value, r.flight_cnt AS recent_flights,
            p.flight_cnt AS previous_flights, r.observed AS recent_observed, p.observed AS previous_observed
          FROM buckets r JOIN buckets p ON r.dimension_value=p.dimension_value
          WHERE r.bucket='recent' AND p.bucket='previous'
          ORDER BY delta_value DESC, r.dimension_value LIMIT ?"""
        rows = self._query(sql, (w['start'], w['previous_start'], w['end'], min_flights, limit))
        for row in rows:
            # Exploratory screening, not a multiple-testing-corrected causal finding.
            if metric.endswith('rate'):
                a, b = row['recent_value'], row['previous_value']
                se = math.sqrt(a*(1-a)/row['recent_observed'] + b*(1-b)/row['previous_observed'])
                row['delta_ci95'] = [row['delta_value']-1.96*se, row['delta_value']+1.96*se]
                row['flagged'] = row['delta_value'] >= .03 and row['delta_ci95'][0] > 0
            else:
                row['flagged'] = row['delta_value'] >= 5
        return {"anomalies": rows, "metric": metric, "dimension": dimension, "period_days": period_days,
                "data_period": w, "sql": sql, "params": [w['start'], w['previous_start'], w['end'], min_flights, limit],
                "note": "探索性筛查，未校正多重比较；需排查航线构成、季节及样本变化。", "error": None}


class WeatherImpactSkill(_SQLiteSkill):
    def analyze(self, precipitation_threshold=2.0, period_days=90, origin=None, carrier=None):
        if not 0 <= precipitation_threshold <= 1000:
            raise ValueError("Invalid precipitation threshold")
        w = window(self.db_path, period_days)
        scope, values = scope_clause(origin, carrier)
        sql = f"""SELECT CASE
            WHEN prcp_ORIGIN > ? OR prcp_DEST > ? THEN 'high_precipitation'
            WHEN prcp_ORIGIN IS NULL OR prcp_DEST IS NULL THEN 'unknown'
            ELSE 'low_precipitation' END AS weather_bucket, {KPI_SELECT}
            FROM flights_enriched WHERE FL_DATE BETWEEN ? AND ? {scope}
            GROUP BY 1 ORDER BY 1"""
        rows = self._query(sql, (precipitation_threshold, precipitation_threshold, w['start'], w['end'], *values))
        buckets = {r['weather_bucket']: r for r in rows}
        high, low = buckets.get('high_precipitation'), buckets.get('low_precipitation')
        delta = None
        if high and low and high['arrival_observed'] >= 30 and low['arrival_observed'] >= 30:
            delta = high['arr_delay_rate']-low['arr_delay_rate']
        return {"buckets": rows, "delta_rate": delta, "period_days": period_days,
                "precipitation_threshold": precipitation_threshold, "data_period": w,
                "interpretation": "降水分组仅表示相关性，未控制机场、航线、时段等混杂因素；缺失天气单列。" if delta is not None else "分组有效样本不足，不输出天气效应结论。", "error": None}


class ReportSkill(_SQLiteSkill):
    def generate(self, period="weekly"):
        if period not in ('daily', 'weekly'):
            raise ValueError("period must be daily or weekly")
        w = window(self.db_path, 7 if period == 'weekly' else 1)
        row = self._query(f"SELECT {KPI_SELECT} FROM flights_enriched WHERE FL_DATE BETWEEN ? AND ?", (w['start'], w['end']))[0]
        row.update(period_start=w['start'], period_end=w['end'])
        report = (f"## 运营{'周报' if period == 'weekly' else '日报'}\n\n"
                  f"观测期：{w['start']} 至 {w['end']}\n\n"
                  f"- 计划航班：{row['flight_cnt']}；有效到达观测：{row['arrival_observed']}\n"
                  f"- 到达延误率：{fmt(row['arr_delay_rate'], True)}\n"
                  f"- 取消率：{fmt(row['cancel_rate'], True)}\n"
                  f"- 平均到达延误：{fmt(row['avg_arr_delay'])} 分钟\n")
        return {"report": report, "metrics": row, "period": period, "error": None}
