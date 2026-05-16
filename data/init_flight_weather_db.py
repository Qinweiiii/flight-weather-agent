"""Initialize SQLite database from local flight+weather CSV.

Usage:
    python data/init_flight_weather_db.py
    python data/init_flight_weather_db.py --csv data/Flight_Data_With_Weather_Final.csv --db data/flight_weather.db
"""

import argparse
import csv
import os
import sqlite3
from typing import Dict, List, Tuple, Any


DEFAULT_CSV_PATH = os.path.join(os.path.dirname(__file__), "Flight_Data_With_Weather_Final.csv")
DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "flight_weather.db")
BATCH_SIZE = 5000

# Keep a practical subset for analytics + interview demos.
SCHEMA: List[Tuple[str, str]] = [
    ("FL_DATE", "TEXT"),
    ("YEAR", "INTEGER"),
    ("MONTH", "INTEGER"),
    ("DAY_OF_WEEK", "INTEGER"),
    ("OP_UNIQUE_CARRIER", "TEXT"),
    ("OP_CARRIER_FL_NUM", "INTEGER"),
    ("ORIGIN", "TEXT"),
    ("ORIGIN_CITY_NAME", "TEXT"),
    ("ORIGIN_STATE_ABR", "TEXT"),
    ("DEST", "TEXT"),
    ("DEST_CITY_NAME", "TEXT"),
    ("DEST_STATE_ABR", "TEXT"),
    ("CRS_DEP_TIME", "INTEGER"),
    ("DEP_TIME", "INTEGER"),
    ("DEP_DELAY", "REAL"),
    ("DEP_DEL15", "INTEGER"),
    ("CRS_ARR_TIME", "INTEGER"),
    ("ARR_TIME", "INTEGER"),
    ("ARR_DELAY", "REAL"),
    ("ARR_DEL15", "INTEGER"),
    ("CANCELLED", "INTEGER"),
    ("CANCELLATION_CODE", "TEXT"),
    ("DIVERTED", "INTEGER"),
    ("DISTANCE", "REAL"),
    ("CARRIER_DELAY", "REAL"),
    ("WEATHER_DELAY", "REAL"),
    ("NAS_DELAY", "REAL"),
    ("SECURITY_DELAY", "REAL"),
    ("LATE_AIRCRAFT_DELAY", "REAL"),
    ("temp_ORIGIN", "REAL"),
    ("prcp_ORIGIN", "REAL"),
    ("wspd_ORIGIN", "REAL"),
    ("wpgt_ORIGIN", "REAL"),
    ("pres_ORIGIN", "REAL"),
    ("snow_ORIGIN", "REAL"),
    ("coco_ORIGIN", "TEXT"),
    ("temp_DEST", "REAL"),
    ("prcp_DEST", "REAL"),
    ("wspd_DEST", "REAL"),
    ("wpgt_DEST", "REAL"),
    ("pres_DEST", "REAL"),
    ("snow_DEST", "REAL"),
    ("coco_DEST", "TEXT"),
]


def _to_int(value: str) -> Any:
    if value is None:
        return None
    v = value.strip()
    if v == "":
        return None
    return int(float(v))


def _to_float(value: str) -> Any:
    if value is None:
        return None
    v = value.strip()
    if v == "":
        return None
    return float(v)


def _to_text(value: str) -> Any:
    if value is None:
        return None
    v = value.strip()
    return v if v != "" else None


def _convert_row(row: Dict[str, str]) -> Tuple[Any, ...]:
    out: List[Any] = []
    for col, dtype in SCHEMA:
        raw = row.get(col)
        if dtype == "INTEGER":
            out.append(_to_int(raw))
        elif dtype == "REAL":
            out.append(_to_float(raw))
        else:
            out.append(_to_text(raw))
    return tuple(out)


def create_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS flights_enriched")

    cols_sql = ",\n        ".join([f"{c} {t}" for c, t in SCHEMA])
    cur.execute(
        f"""
        CREATE TABLE flights_enriched (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {cols_sql}
        )
        """
    )

    cur.execute("CREATE INDEX IF NOT EXISTS idx_flights_date ON flights_enriched(FL_DATE)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_flights_carrier ON flights_enriched(OP_UNIQUE_CARRIER)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_flights_origin ON flights_enriched(ORIGIN)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_flights_dest ON flights_enriched(DEST)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_flights_cancelled ON flights_enriched(CANCELLED)")

    cur.execute("DROP VIEW IF EXISTS v_flight_monthly_kpi")
    cur.execute(
        """
        CREATE VIEW v_flight_monthly_kpi AS
        SELECT
            substr(FL_DATE, 1, 7) AS ym,
            OP_UNIQUE_CARRIER,
            COUNT(*) AS flight_cnt,
            AVG(COALESCE(DEP_DELAY, 0)) AS avg_dep_delay,
            AVG(COALESCE(ARR_DELAY, 0)) AS avg_arr_delay,
            AVG(COALESCE(DEP_DEL15, 0)) AS dep_delay_rate,
            AVG(COALESCE(ARR_DEL15, 0)) AS arr_delay_rate,
            AVG(COALESCE(CANCELLED, 0)) AS cancel_rate,
            AVG(COALESCE(DIVERTED, 0)) AS divert_rate
        FROM flights_enriched
        GROUP BY ym, OP_UNIQUE_CARRIER
        ORDER BY ym, OP_UNIQUE_CARRIER
        """
    )

    conn.commit()


def import_csv(csv_path: str, db_path: str) -> None:
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        create_schema(conn)
        cur = conn.cursor()

        col_names = [c for c, _ in SCHEMA]
        placeholders = ",".join(["?" for _ in col_names])
        insert_sql = f"INSERT INTO flights_enriched ({','.join(col_names)}) VALUES ({placeholders})"

        inserted = 0
        batch: List[Tuple[Any, ...]] = []

        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                batch.append(_convert_row(row))
                if len(batch) >= BATCH_SIZE:
                    cur.executemany(insert_sql, batch)
                    inserted += len(batch)
                    batch = []

            if batch:
                cur.executemany(insert_sql, batch)
                inserted += len(batch)

        conn.commit()

        # Quick profiling output for confidence.
        stats = cur.execute(
            """
            SELECT
                COUNT(*) AS n,
                MIN(FL_DATE) AS min_date,
                MAX(FL_DATE) AS max_date,
                COUNT(DISTINCT OP_UNIQUE_CARRIER) AS carriers,
                COUNT(DISTINCT ORIGIN) AS origins,
                COUNT(DISTINCT DEST) AS dests
            FROM flights_enriched
            """
        ).fetchone()

        print("=" * 72)
        print("Flight+Weather DB initialized successfully")
        print(f"DB Path      : {db_path}")
        print(f"Rows Inserted: {inserted}")
        print(f"Date Range   : {stats[1]} -> {stats[2]}")
        print(f"Carriers     : {stats[3]}")
        print(f"Origins      : {stats[4]}")
        print(f"Dests        : {stats[5]}")
        print("Main table   : flights_enriched")
        print("KPI view     : v_flight_monthly_kpi")
        print("=" * 72)
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize flight weather SQLite database")
    parser.add_argument("--csv", default=DEFAULT_CSV_PATH, help="Path to source CSV")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Path to target SQLite DB")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    import_csv(csv_path=args.csv, db_path=args.db)


if __name__ == "__main__":
    main()
