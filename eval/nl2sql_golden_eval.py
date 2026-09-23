"""Versioned NL2SQL execution evaluation; offline checks are not model accuracy."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
import time
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.sql_executor import read_rows
from agents.sql_agent import SQLQueryAgent
from langchain_openai import ChatOpenAI


def normalize_results(data, ordered=False):
    if isinstance(data,str):
        data=json.loads(data)
    if not isinstance(data,list) or any(not isinstance(row,dict) for row in data):
        raise ValueError('Expected row list, not an error/message object')
    def scalar(value):
        if value is None:
            return ('null',)
        if isinstance(value,(int,float)):
            return ('number', str(Decimal(str(value)).quantize(Decimal('.00001'))))
        return ('text',str(value))
    # Projection order matters; column aliases do not. Duplicates and NULLs survive.
    rows=[tuple(scalar(v) for v in row.values()) for row in data]
    return rows if ordered else Counter(rows)


GOLDEN_DATASET = json.loads((ROOT/'eval/golden_v2.json').read_text(encoding='utf-8'))['cases']


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db')
    parser.add_argument('--limit',type=int)
    parser.add_argument('--validate-only',action='store_true')
    parser.add_argument('--output')
    parser.add_argument('--timeout',type=int,default=60)
    args=parser.parse_args()
    cfg=yaml.safe_load((ROOT/'config/config.yaml').read_text(encoding='utf-8'))
    db=(ROOT/(args.db or os.getenv('FLIGHT_DB_PATH') or cfg['database']['path'])).resolve()
    model=os.getenv('LLM_MODEL',cfg['llm']['model'])
    agent=None
    if not args.validate_only:
        key=os.getenv('QWEN_API_KEY') or os.getenv('DASHSCOPE_API_KEY')
        if not key:
            parser.error('Set QWEN_API_KEY for model evaluation, or use --validate-only')
        llm=ChatOpenAI(model=model,api_key=key,base_url=os.getenv('LLM_BASE_URL',cfg['llm'].get('base_url','https://dashscope.aliyuncs.com/compatible-mode/v1')),
                       temperature=0,max_tokens=2048,timeout=args.timeout,max_retries=0)
        agent=SQLQueryAgent(llm,str(db),num_examples=3)
    cases=[]
    for item in GOLDEN_DATASET[:args.limit]:
        started=time.monotonic()
        record={'id':item['id'],'question':item['question'],'expected_sql':item['expected_sql']}
        try:
            expected=read_rows(db,item['expected_sql'])
            record['expected_rows']=len(expected)
            if args.validate_only:
                record['status']='reference_valid' if expected else 'reference_empty'
            else:
                result=agent.query(item['question'],max_retries=2)
                record.update(generated_sql=result.get('sql'),retry_count=result.get('retry_count'),attempts=result.get('attempts'),failure_stage=result.get('failure_stage'),error=result.get('error'))
                if result.get('error'):
                    record['status']='failed'
                elif not expected:
                    record['status']='inconclusive_empty_reference'
                else:
                    record['status']='passed' if normalize_results(result['data'],item.get('ordered',False))==normalize_results(expected,item.get('ordered',False)) else 'mismatch'
        except Exception as exc:
            record.update(status='reference_or_harness_error',error=str(exc))
        record['latency_ms']=round((time.monotonic()-started)*1000,1)
        cases.append(record)
        print(item['id'],record['status'],record['latency_ms'])
    scored=[c for c in cases if c['status'] in ('passed','failed','mismatch')]
    accuracy=sum(c['status']=='passed' for c in scored)/len(scored) if scored else None
    result={'timestamp':datetime.now(timezone.utc).isoformat(),'dataset_version':2,'db':db.name,
            'model':None if args.validate_only else model,'mode':'reference_validation' if args.validate_only else 'live_nl2sql',
            'execution_accuracy':accuracy,'scored_cases':len(scored),'total_cases':len(cases),'cases':cases}
    output=Path(args.output) if args.output else ROOT/'eval/results'/('golden-validation.json' if args.validate_only else 'live-nl2sql.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Report: {output}; EX accuracy: {accuracy}')
    return int(any(c['status'] in ('failed','mismatch','reference_or_harness_error') for c in cases))


if __name__=='__main__':
    raise SystemExit(main())
