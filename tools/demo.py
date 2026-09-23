"""Explicit fixed scenarios, never a pretend LLM or fake search provider."""
import re
from tools.metrics import METRICS, KPI_SELECT


def demo_route(question):
    if re.search(r'20\d{2}|今天|明天|下周|实时|到达机场|航线|超过|大于|最低|最多|总数|多少', question):
        raise ValueError('离线固定场景不支持此筛选；请使用 README 的演示问题，或启用真实模型模式。')
    codes = re.findall(r'(?<![A-Za-z])[A-Z]{2,3}(?![A-Za-z])', question)
    if set(codes)-{'AAA','BBB','CCC','DDD','ZX','ZY','SQL'}:
        raise ValueError('离线场景仅含 AAA/BBB/CCC/DDD 机场与 ZX/ZY 航司')
    days_match = re.search(r'(?:近|最近)(\d+)天', question)
    days = int(days_match[1]) if days_match else 30
    origin = next((a for a in ('AAA','BBB','CCC','DDD') if a in question.upper()), None)
    carrier = next((c for c in ('ZX','ZY') if c in question.upper()), None)
    if any(s in question for s in ('行业', '基准', '联网')):
        intent, params = 'search_and_sql', {}
    elif '异常' in question:
        intent, params = 'ops_anomaly', {'period_days': days, 'metric': 'cancel_rate' if '取消' in question else 'arr_delay_rate'}
    elif '周报' in question or '日报' in question:
        intent, params = 'ops_report', {'period': 'daily' if '日报' in question else 'weekly'}
    elif any(s in question for s in ('诊断', '原因', '天气', '建议', '复盘', '优化')):
        intent, params = 'ops_diagnosis', {'period_days': days, 'origin': origin, 'carrier': carrier}
    elif '图' in question:
        intent, params = 'sql_and_analysis', {}
    elif any(s in question for s in ('航班', '机场', '延误率', '取消率')):
        intent, params = 'sql_only', {}
    else:
        intent, params = 'simple_answer', {}
    return {'intent': intent, 'params': params, 'reason': 'offline_fixed_scenarios_not_llm_routing'}


def demo_sql(question):
    if any(s in question for s in ('航班', '机场', '延误率', '取消率', '行业', '基准', '图')):
        metric = METRICS['cancel_rate'] if '取消' in question else METRICS['arr_delay_rate']
        match = re.search(r'(?:近|最近)(\d+)天', question)
        days = int(match[1]) if match else 30
        if not 1 <= days <= 366:
            raise ValueError('period_days must be 1..366')
        return f"SELECT ORIGIN, COUNT(*) AS flight_cnt, {metric} AS metric_value FROM flights_enriched WHERE FL_DATE >= date((SELECT MAX(FL_DATE) FROM flights_enriched), '-{days-1} days') GROUP BY ORIGIN ORDER BY metric_value DESC, ORIGIN"
    return None
