"""Metric contract: explicit denominators, calendar windows and missingness."""
from datetime import date, timedelta
from tools.sql_executor import read_rows

ARR_VALID = "CANCELLED = 0 AND DIVERTED = 0 AND ARR_DELAY IS NOT NULL"
METRICS = {
    "arr_delay_rate": f"AVG(CASE WHEN {ARR_VALID} THEN CASE WHEN ARR_DELAY >= 15 THEN 1.0 ELSE 0.0 END END)",
    "cancel_rate": "AVG(CASE WHEN CANCELLED IN (0,1) THEN CANCELLED * 1.0 END)",
    "avg_arr_delay": f"AVG(CASE WHEN {ARR_VALID} THEN ARR_DELAY END)",
}
KPI_SELECT = f"""COUNT(*) AS flight_cnt,
    SUM(CASE WHEN {ARR_VALID} THEN 1 ELSE 0 END) AS arrival_observed,
    {METRICS['arr_delay_rate']} AS arr_delay_rate,
    {METRICS['cancel_rate']} AS cancel_rate,
    {METRICS['avg_arr_delay']} AS avg_arr_delay,
    SUM(CASE WHEN {ARR_VALID} THEN MAX(ARR_DELAY, 0) ELSE 0 END) AS positive_arr_delay_minutes,
    AVG(CASE WHEN CANCELLED=0 AND DEP_DELAY IS NOT NULL THEN DEP_DELAY END) AS avg_dep_delay_min,
    AVG(CASE WHEN CANCELLED=0 AND DEP_DELAY IS NOT NULL THEN CASE WHEN DEP_DELAY>=15 THEN 1.0 ELSE 0.0 END END) AS dep_delay_rate,
    AVG(CASE WHEN DIVERTED IN (0,1) THEN DIVERTED * 1.0 END) AS divert_rate"""


def window(db_path, days=30):
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 366:
        raise ValueError("period_days must be an integer in 1..366")
    period = read_rows(db_path, "SELECT MIN(FL_DATE) AS min_date, MAX(FL_DATE) AS max_date FROM flights_enriched")[0]
    if not period['max_date']:
        raise ValueError("empty_dataset")
    end = date.fromisoformat(period['max_date'][:10])
    start = end - timedelta(days=days-1)
    return {**period, "start": str(start), "end": str(end),
            "previous_start": str(start-timedelta(days=days)),
            "previous_end": str(start-timedelta(days=1)), "days": days}


def scope_clause(origin=None, carrier=None):
    clauses, params = [], []
    for column, value in (("ORIGIN", origin), ("OP_UNIQUE_CARRIER", carrier)):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value.upper())
    return (" AND " + " AND ".join(clauses) if clauses else ""), tuple(params)


def provenance(db_path):
    tables = read_rows(db_path, "SELECT name FROM sqlite_master WHERE type='table' AND name='dataset_metadata'")
    if tables:
        return {row['key']: row['value'] for row in read_rows(db_path, "SELECT key,value FROM dataset_metadata")}
    return {"kind": "historical", "source": "local CSV; original provenance not independently verified", "label": "历史样本（来源待核验）"}


def fmt(value, percent=False):
    return "无有效观测" if value is None else (f"{value:.2%}" if percent else f"{value:,.2f}")
