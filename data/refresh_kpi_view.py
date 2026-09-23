"""Upgrade only the KPI view, preserving all historical rows."""
import argparse
from pathlib import Path
import sqlite3
from init_flight_weather_db import create_kpi_view

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=str(Path(__file__).parent/'flight_weather.db'))
    args = parser.parse_args()
    path = Path(args.db).resolve()
    with sqlite3.connect(path.as_uri()+'?mode=rw',uri=True) as conn:
        before = conn.execute('SELECT COUNT(*) FROM flights_enriched').fetchone()[0]
        create_kpi_view(conn)
        after = conn.execute('SELECT COUNT(*) FROM flights_enriched').fetchone()[0]
        assert before == after
        print(f'Updated view; {after} source rows preserved: {path}')
