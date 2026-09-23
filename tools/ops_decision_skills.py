"""Evidence-backed diagnosis and explicitly hypothetical action scenarios."""
from pathlib import Path
import yaml
from tools.sql_executor import read_rows
from tools.metrics import ARR_VALID, KPI_SELECT, window, scope_clause, provenance, fmt
from tools.operational_skills import WeatherImpactSkill

COMPONENTS = (
    ('carrier_operations', '航司报告原因', 'CARRIER_DELAY'),
    ('weather', '报告天气原因', 'WEATHER_DELAY'),
    ('airport_congestion', 'NAS 综合原因（含天气、流控等）', 'NAS_DELAY'),
    ('security', '安保原因', 'SECURITY_DELAY'),
    ('late_aircraft', '前序飞机晚到（传播表征）', 'LATE_AIRCRAFT_DELAY'),
)


class RootCauseSkill:
    def __init__(self, db_path):
        self.db_path = db_path

    def diagnose(self, period_days=30, origin=None, carrier=None):
        w = window(self.db_path, period_days)
        scope, values = scope_clause(origin, carrier)
        params = (w['start'], w['end'], *values)
        where = f"FL_DATE BETWEEN ? AND ? {scope}"
        base = read_rows(self.db_path, f"SELECT {KPI_SELECT} FROM flights_enriched WHERE {where}", params)[0]
        previous = read_rows(self.db_path, f"SELECT {KPI_SELECT} FROM flights_enriched WHERE {where}",
                             (w['previous_start'], w['previous_end'], *values))[0]
        fields = ', '.join(f"SUM(CASE WHEN {ARR_VALID} AND ARR_DELAY>=15 AND {col}>=0 THEN {col} END) AS {col}" for _, _, col in COMPONENTS)
        fields += f", SUM(CASE WHEN {ARR_VALID} AND ARR_DELAY>=15 THEN 1 ELSE 0 END) AS delayed_flights"
        complete = ' AND '.join(f'{col} IS NOT NULL AND {col}>=0' for _, _, col in COMPONENTS)
        fields += f", SUM(CASE WHEN {ARR_VALID} AND ARR_DELAY>=15 AND {complete} THEN 1 ELSE 0 END) AS reported_flights"
        reported = read_rows(self.db_path, f"SELECT {fields} FROM flights_enriched WHERE {where}", params)[0]
        total = sum(reported[c] or 0 for _, _, c in COMPONENTS)
        factors = [{"factor": key, "label": label, "minutes": reported[col] or 0,
                    "score": (reported[col] or 0)/total if total else None,
                    "share": (reported[col] or 0)/total if total else None,
                    "evidence": f"{col} 累计 {fmt(reported[col])} 分钟（报告原因记录，不等于因果效应）。"}
                   for key, label, col in COMPONENTS if (reported[col] or 0) > 0]
        factors.sort(key=lambda r: r['minutes'], reverse=True)
        hotspots = read_rows(self.db_path, f"""SELECT ORIGIN, OP_UNIQUE_CARRIER, {KPI_SELECT}
            FROM flights_enriched WHERE {where} GROUP BY ORIGIN, OP_UNIQUE_CARRIER
            HAVING arrival_observed >= 30 ORDER BY positive_arr_delay_minutes DESC, ORIGIN, OP_UNIQUE_CARRIER LIMIT 5""", params)
        coverage = reported['reported_flights']/reported['delayed_flights'] if reported['delayed_flights'] else None
        cancellations = read_rows(self.db_path, f"""SELECT COALESCE(NULLIF(CANCELLATION_CODE,''),'unknown') AS code,
            COUNT(*) AS flight_cnt FROM flights_enriched WHERE {where} AND CANCELLED=1 GROUP BY 1 ORDER BY 2 DESC,1""", params)
        return {"period_days": period_days, "data_period": w, "scope": {"origin": origin, "carrier": carrier},
                "baseline": base, "previous": previous, "ranked_factors": factors, "hotspots": hotspots,
                "reported_minutes": total, "component_coverage": coverage, "cancellation_codes": cancellations,
                "weather": WeatherImpactSkill(self.db_path).analyze(period_days=period_days, origin=origin, carrier=carrier),
                "provenance": provenance(self.db_path), "sql": f"SELECT {KPI_SELECT}, {fields} FROM flights_enriched WHERE {where}",
                "params": list(params),
                "note": "报告原因分解与天气相关性分开呈现。NAS 不能直接等同机场拥堵；前序晚到不能直接等同航司过错。"}


class OpsPlaybookSkill:
    def __init__(self, playbook_path=None):
        path = Path(playbook_path) if playbook_path else Path(__file__).resolve().parents[1]/'config/ops_playbooks.yaml'
        self.playbooks = yaml.safe_load(path.read_text(encoding='utf-8'))['playbooks']

    def recommend(self, diagnosis, top_n=3, assumed_delay_reduction_pct=.08):
        if not 1 <= top_n <= 5 or not 0 <= assumed_delay_reduction_pct <= 1:
            raise ValueError("Invalid scenario parameters")
        selected = []
        coverage = diagnosis.get('component_coverage')
        if coverage is not None and coverage >= .5:
            for factor in diagnosis.get('ranked_factors', []):
                if factor['minutes'] <= 0:
                    continue
                book = self.playbooks.get(factor['factor'], self.playbooks['evidence_gap'])
                selected.append({"factor": factor['factor'], "evidence": factor['evidence'], "playbook": book,
                                 "scenario_minutes": round(factor['minutes']*assumed_delay_reduction_pct, 1),
                                 "validation": "选同机场/航线/时段试点与对照，至少复核两个等长窗口；同时监测取消率、成本及保障约束。"})
                if len(selected) >= top_n:
                    break
        if not selected:
            selected = [{"factor": "evidence_gap", "evidence": "报告原因覆盖不足或无正延误贡献，先补证据。",
                         "playbook": self.playbooks['evidence_gap'], "scenario_minutes": None,
                         "validation": "补齐原因记录并重新诊断。"}]
        minutes = sum(r['scenario_minutes'] or 0 for r in selected)
        return {"recommendations": selected, "impact_estimate": {
            "kind": "hypothetical_not_forecast", "assumed_reduction": assumed_delay_reduction_pct,
            "estimated_minutes_saved": minutes if selected[0]['factor'] != 'evidence_gap' else None,
            "assumption": "仅对所选报告原因分钟数应用用户假设比例；不代表可回收比例或已实现收益，不能换算利润。"},
            "next_step": "运营人员审核建议、补充周转/机组/容量证据，再设计对照试点。系统不执行调度。"}


def render_diagnosis(diagnosis, actions):
    b, p, w = diagnosis['baseline'], diagnosis['previous'], diagnosis['data_period']
    lines = ["## 运营复盘与行动候选", f"数据：{diagnosis['provenance']['label']}；{w['start']} 至 {w['end']}。",
             f"范围：出发机场 {diagnosis['scope']['origin'] or '全部'}；航司 {diagnosis['scope']['carrier'] or '全部'}。",
             f"计划航班 {b['flight_cnt']}，有效到达观测 {b['arrival_observed']}。",
             f"到达延误率 {fmt(b['arr_delay_rate'], True)}，上期 {fmt(p['arr_delay_rate'], True)}；取消率 {fmt(b['cancel_rate'], True)}。",
             "### 证据", f"原因字段完整覆盖率：{fmt(diagnosis['component_coverage'], True)}。"]
    for f in diagnosis['ranked_factors']:
        lines.append(f"- {f['label']}：{fmt(f['minutes'])} 分钟，占已报告原因分钟 {fmt(f['share'], True)}。")
    if not diagnosis['ranked_factors']:
        lines.append("- 无有效原因分钟，不能给出主要原因排名。")
    lines.extend([diagnosis['note'], diagnosis['weather']['interpretation']])
    if diagnosis['cancellation_codes']:
        lines.append('取消记录（独立于到达延误原因分钟）：' + '；'.join(f"代码 {r['code']}：{r['flight_cnt']} 班" for r in diagnosis['cancellation_codes']) + '。需要结合源数据取消代码字典复核，不能用到达延误分类代替取消原因。')
    if diagnosis['weather']['delta_rate'] is not None:
        lines.append(f"高降水与低降水组延误率差：{diagnosis['weather']['delta_rate']*100:.2f} 个百分点（相关性）。")
    lines.append("### 优先复核对象")
    for h in diagnosis['hotspots']:
        lines.append(f"- {h['ORIGIN']} / {h['OP_UNIQUE_CARRIER']}：正到达延误 {fmt(h['positive_arr_delay_minutes'])} 分钟，{h['arrival_observed']} 条有效到达观测。")
    lines.append("### 行动候选与验证")
    for rec in actions['recommendations']:
        lever = rec['playbook']['levers'][0]
        lines.append(f"- {lever['name']}；责任团队：{lever['owner']}；周期：{lever['horizon']}。依据：{rec['evidence']} 验证：{rec['validation']}")
    impact = actions['impact_estimate']
    lines.append(f"情景测算：假设所选原因分钟减少 {impact['assumed_reduction']:.0%}，对应 {fmt(impact['estimated_minutes_saved'])} 分钟。{impact['assumption']}")
    lines.extend([actions['next_step'], "口径：到达延误 >=15 分钟；延误率分母排除取消、备降及缺失到达观测；取消率分母为状态已知的计划航班。"])
    return '\n\n'.join(lines)
