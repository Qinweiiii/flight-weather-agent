"""Run offline regressions and save an honest machine-readable report."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class RecordedResult(unittest.TextTestResult):
    def startTest(self, test):
        self.started = time.monotonic()
        self.outcome = 'passed'
        super().startTest(test)

    def addFailure(self, test, err):
        self.outcome = 'failed'
        super().addFailure(test, err)

    def addError(self, test, err):
        self.outcome = 'failed'
        super().addError(test, err)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self.outcome = 'failed'
        super().addSubTest(test, subtest, err)

    def addSkip(self, test, reason):
        self.outcome = 'skipped'
        super().addSkip(test, reason)

    def stopTest(self, test):
        records.append({'id': test.id(), 'status': self.outcome,
                        'latency_ms': round((time.monotonic()-self.started)*1000,1)})
        super().stopTest(test)


records = []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',default=str(ROOT/'eval/results/offline-regression.json'))
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=2,resultclass=RecordedResult).run(suite)
    path = Path(args.output); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'timestamp':datetime.now(timezone.utc).isoformat(),'python':platform.python_version(),
        'mode':'offline_regression_scripted_llm_not_model_accuracy','tests':result.testsRun,
        'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'cases':records,
        'live_nl2sql_accuracy':None,'live_search_validated':False},ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
