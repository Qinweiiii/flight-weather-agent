"""Reproducible synthetic data with distinct, testable operational scenarios."""
import argparse
from datetime import date, timedelta
import json
from pathlib import Path
import random
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data.init_flight_weather_db import create_schema, SCHEMA


def generate(db_path, seed=42):
    path = Path(db_path)
    if path.exists():
        raise FileExistsError(f'{path} already exists; choose a new path to avoid overwriting data')
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    rows = []
    start = date(2025, 1, 1)
    for day in range(90):
        recent = day >= 60
        for airport in ('AAA', 'BBB', 'CCC', 'DDD'):
            for number in range(40):
                carrier = 'ZX' if number % 2 else 'ZY'
                wet = rng.random() < (.75 if airport == 'AAA' and recent else .15)
                cancelled = int(rng.random() < (.16 if airport == 'CCC' and recent else .01))
                diverted = int(not cancelled and rng.random() < .005)
                components = dict(CARRIER_DELAY=0., WEATHER_DELAY=0., NAS_DELAY=0., SECURITY_DELAY=0., LATE_AIRCRAFT_DELAY=0.)
                if wet and rng.random() < .75:
                    components['WEATHER_DELAY'] = float(rng.randint(20,60))
                if airport == 'BBB' and carrier == 'ZX' and recent and rng.random() < .85:
                    components['LATE_AIRCRAFT_DELAY'] = float(rng.randint(35,90))
                    components['CARRIER_DELAY'] = float(rng.randint(10,25))
                if rng.random() < .15:
                    components['NAS_DELAY'] = float(rng.randint(15,30))
                arrival = sum(components.values()) or float(rng.randint(-12,14))
                if cancelled or diverted:
                    arrival = None
                    components = {key: None for key in components}
                row = dict(FL_DATE=str(start+timedelta(days=day)), YEAR=2025, MONTH=(start+timedelta(days=day)).month,
                           DAY_OF_WEEK=(start+timedelta(days=day)).isoweekday(), ORIGIN=airport, DEST='DDD' if airport!='DDD' else 'AAA',
                           OP_UNIQUE_CARRIER=carrier, OP_CARRIER_FL_NUM=100+number,
                           CANCELLED=cancelled, DIVERTED=diverted, CANCELLATION_CODE='A' if cancelled else None,
                           ARR_DELAY=arrival, ARR_DEL15=int(arrival>=15) if arrival is not None else None,
                           DEP_DELAY=arrival, DEP_DEL15=int(arrival>=15) if arrival is not None else None,
                           prcp_ORIGIN=8. if wet else (None if rng.random()<.03 else 0.), prcp_DEST=0.,
                           wspd_ORIGIN=12., wspd_DEST=10., CRS_DEP_TIME=((360+number*15)//60)*100+(360+number*15)%60, **components)
                rows.append(tuple(row.get(col) for col,_ in SCHEMA))
    with sqlite3.connect(path) as conn:
        create_schema(conn)
        columns = ','.join(c for c,_ in SCHEMA)
        conn.executemany(f"INSERT INTO flights_enriched ({columns}) VALUES ({','.join('?' for _ in SCHEMA)})", rows)
        conn.execute('CREATE TABLE dataset_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        metadata = {'kind': 'synthetic', 'label': '虚拟运营场景（非真实航班）', 'source': 'generate_demo_data.py',
                    'seed': str(seed), 'scenario': 'AAA weather; BBB/ZX late aircraft; CCC cancellations; DDD control'}
        conn.executemany('INSERT INTO dataset_metadata VALUES (?,?)', metadata.items())
    return {'rows': len(rows), 'path': str(path), **metadata}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=str(Path(__file__).parent/'demo_operations.db'))
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    print(json.dumps(generate(args.db, args.seed), ensure_ascii=False, indent=2))
