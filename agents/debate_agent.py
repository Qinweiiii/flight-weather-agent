"""
Debate Agent

对来自不同子智能体的候选答案进行对比和裁决，输出统一结论。
"""

import re
import json
from typing import Dict, Any


class DebateAgent:
    """多路径裁决智能体"""

    def __init__(self, llm: Any):
        self.llm = llm

    @staticmethod
    def _llm_to_str(result) -> str:
        import re
        if isinstance(result, str):
            text = result
        elif hasattr(result, "content"):
            text = str(result.content)
        elif hasattr(result, "text"):
            text = str(result.text)
        else:
            text = str(result)
        text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
        text = re.sub(r"</think>", "", text).strip()
        return text

    @staticmethod
    def _extract_year_span(text: str) -> str:
        years = sorted({int(y) for y in re.findall(r"\b(20\d{2})\b", text or "")})
        if not years:
            return "unknown"
        if len(years) == 1:
            return str(years[0])
        return f"{years[0]}-{years[-1]}"

    @staticmethod
    def _parse_span(span: str):
        """将 '2016-2018' 或 '2018' 解析为数值区间。"""
        if not span or span == "unknown":
            return None
        if "-" in span:
            a, b = span.split("-", 1)
            try:
                return int(a), int(b)
            except Exception:
                return None
        try:
            year = int(span)
            return year, year
        except Exception:
            return None

    def _scorecard(self, question: str, sql_evidence: str, search_evidence: str, internal_span: str, external_span: str) -> Dict[str, Any]:
        """基于可解释规则给出裁决分数卡，减少纯prompt拍脑袋。"""
        q = (question or "").lower()
        sql_text = (sql_evidence or "").lower()
        web_text = (search_evidence or "").lower()

        # 1) 内部数据可用性（0-5）
        has_sql_error = "error" in sql_text or "失败" in sql_text
        has_sql_signal = any(t in sql_text for t in ["flight", "航班", "delay", "延误", "airport", "机场", "avg", "sum", "count", "{"])
        sql_score = 1 if has_sql_error else (4 if has_sql_signal else 2)

        # 2) 外部证据充分性（0-5）
        weak_terms = ["证据不足", "无法给出确定性", "来源少", "口径不一致"]
        has_weak = any(t in web_text for t in weak_terms)
        src_count = len(re.findall(r"https?://", search_evidence or ""))
        if has_weak:
            external_score = 1
        elif src_count >= 2:
            external_score = 4
        else:
            external_score = 3 if web_text.strip() else 1

        # 3) 行业口径匹配（0-5）
        aviation_terms = ["航班", "航空", "机场", "延误", "取消", "准点", "airline", "airport", "flight", "on-time", "delay", "weather", "faa", "bts"]
        off_topic_terms = ["房地产", "hospital", "医疗", "fashion", "cosmetics"]
        match_hits = sum(1 for t in aviation_terms if t in web_text)
        off_hits = sum(1 for t in off_topic_terms if t in web_text)
        industry_score = 4 if match_hits >= 2 else (3 if match_hits >= 1 else 2)
        industry_score = max(1, industry_score - off_hits)

        # 4) 时间可比性（0-5）
        i_span = self._parse_span(internal_span)
        e_span = self._parse_span(external_span)
        if not i_span or not e_span:
            time_score = 2
            time_reason = "时间范围识别不足"
        else:
            # 区间交集判断
            overlap = not (i_span[1] < e_span[0] or e_span[1] < i_span[0])
            if overlap:
                time_score = 5
                time_reason = "内外部时间区间可比"
            else:
                gap = min(abs(i_span[0] - e_span[1]), abs(e_span[0] - i_span[1]))
                time_score = 1 if gap >= 3 else 2
                time_reason = f"时间区间不重叠，最小间隔约{gap}年"

        total = sql_score + external_score + industry_score + time_score
        threshold = 13
        if total >= threshold:
            decision = "proceed"
        elif total >= 10 and external_score >= 3 and industry_score >= 3:
            decision = "proceed_with_caveat"
        else:
            decision = "low_evidence"

        return {
            "sql_score": sql_score,
            "external_score": external_score,
            "industry_score": industry_score,
            "time_score": time_score,
            "time_reason": time_reason,
            "total": total,
            "threshold": threshold,
            "decision": decision,
            "question_is_comparison": any(k in q for k in ["对比", "基准", "行业", "均值", "benchmark"])
        }

    @staticmethod
    def _extract_internal_signal(sql_evidence: str) -> str:
        """从 SQL 证据中提取可展示的内部现状片段。"""
        try:
            payload = json.loads(sql_evidence)
            if not isinstance(payload, list) or not payload:
                return ""

            # 优先识别按航司聚合的延误率字段
            samples = []
            for row in payload[:20]:
                if not isinstance(row, dict):
                    continue
                carrier = row.get("OP_UNIQUE_CARRIER") or row.get("op_unique_carrier")
                rate = (
                    row.get("arr_delay_rate")
                    or row.get("ARR_DELAY_RATE")
                    or row.get("arr_del15_rate")
                    or row.get("ARR_DEL15_RATE")
                )
                if carrier is None or rate is None:
                    continue
                try:
                    pct = float(rate) * 100
                    samples.append((str(carrier), pct))
                except Exception:
                    continue

            if samples:
                samples.sort(key=lambda x: x[1], reverse=True)
                top = ", ".join([f"{c}: {v:.1f}%" for c, v in samples[:3]])
                return f"基于现有内部数据，当前航司到达延误率样例为：{top}。"

            # 次优：若有平均到达延误分钟数
            for row in payload[:10]:
                if not isinstance(row, dict):
                    continue
                if "avg_arr_delay" in row:
                    try:
                        return f"基于现有内部数据，平均到达延误约 {float(row['avg_arr_delay']):.1f} 分钟。"
                    except Exception:
                        pass
        except Exception:
            return ""

        return ""

    def _low_evidence_answer(self, scorecard: Dict[str, Any], sql_evidence: str) -> str:
        """分数不达标时，输出结构化、可执行的降级回答。"""
        internal_line = self._extract_internal_signal(sql_evidence)
        if not internal_line:
            internal_line = "基于现有内部数据，可先确认主要异常航司/机场后再做行业对标。"

        return (
            "当前证据不足，暂不建议给出确定性的行业对标结论。原因如下：\n\n"
            f"- 裁决评分 {scorecard.get('total', 0)}/{scorecard.get('threshold', 13)}（未达阈值）\n"
            f"- 时间可比性：{scorecard.get('time_reason', '未提供可比时间口径')}\n"
            f"- 外部证据分：{scorecard.get('external_score', 0)}，行业口径分：{scorecard.get('industry_score', 0)}，表明高相关外部来源不足以支撑量化对标\n"
            f"- {internal_line}\n\n"
            "建议：\n\n"
            "1. 明确指定同地区同年份（如美国 2025）行业口径；\n"
            "2. 指定可比指标（到达延误率、取消率、改降率、天气延误占比）；\n"
            "3. 引入至少2个高相关外部来源（如 FAA/BTS 官方或行业报告）后再做量化对标。"
        )

    def adjudicate(self, question: str, sql_evidence: str, search_evidence: str,
                   internal_period=None, search_quality=None, sql_error=None, sources=None, synthetic=False) -> Dict[str, Any]:
        """裁决两路证据，输出最终统一回答。"""
        internal_span = self._extract_year_span(sql_evidence)
        external_span = self._extract_year_span(search_evidence)

        scorecard = self._scorecard(question, sql_evidence, search_evidence, internal_span, external_span)
        # A soft score cannot compensate for missing evidence or incomparable data.
        if internal_period:
            internal_span = self._extract_year_span(str(internal_period.get('min_date')) + ' ' + str(internal_period.get('max_date')))
            scorecard = self._scorecard(question, sql_evidence, search_evidence, internal_span, external_span)
        reasons = []
        if synthetic:
            reasons.append('虚拟数据不能用于真实行业高低判断')
        if sql_error or not sql_evidence or sql_evidence.strip() == '[]':
            reasons.append('内部查询失败或为空')
        if search_quality is not None and not search_quality.get('evidence_enough', False):
            reasons.append('外部证据不足或搜索未启用')
        if sources is not None and not sources:
            reasons.append('无可追溯外部来源')
        if scorecard.get('time_score', 0) < 5:
            reasons.append('观测期未知或不重叠')
        # Search snippets do not establish a matching population and denominator.
        if search_quality is not None and not search_quality.get('metric_contract_verified', False):
            reasons.append('指标分母、地区和样本总体尚未核验')
        if reasons:
            scorecard.update(decision='low_evidence', blocking_reasons=reasons)
            return {'answer': '暂不能给出确定性的行业高低判断。\n\n' + '\n'.join('- '+r for r in reasons)
                    + '\n\n内部查询证据：\n```json\n' + (sql_evidence or '[]')[:6000] + '\n```\n\n下一步：取得同观测期、同地区、同延误阈值及分母的原始统计，再作对比。',
                    'status': 'low_evidence', 'scorecard': scorecard}
        if scorecard.get("decision") == "low_evidence":
            return {
                "answer": self._low_evidence_answer(scorecard, sql_evidence),
                "status": "low_evidence",
                "scorecard": scorecard
            }

        prompt = f"""你是裁决智能体。请根据两路证据输出统一回答。

用户问题：{question}

证据A（内部SQL证据）：
{sql_evidence}

内部证据时间范围（自动识别）：{internal_span}

证据B（外部搜索证据）：
{search_evidence}

外部证据时间范围（自动识别）：{external_span}

裁决评分卡：{scorecard}

请遵循：
1. 优先保证事实一致，不编造数据
2. 对冲突信息给出解释
3. 如果内外部证据的时间跨度不一致（例如内部是2016-2018，外部是2026），必须明确提示“不可直接横向比较”
4. 时间不一致时，优先给出“趋势性/方向性”解读，并说明可比口径（同年对比、同比增速、指数化）
5. 如果外部证据明显不是航班/航空行业口径（如其他行业均值），不得用于航班行业基准结论
6. 若外部证据数量或质量不足，必须输出“证据不足，无法给出确定性行业对标结论”，并给出需要补充的数据项
7. 输出结构：先结论，再依据，再建议
8. 仅输出最终回答，不要解释过程
9. 若 decision=proceed_with_caveat，必须在结论中明确写“代理基准（方向性参考）”，不可写“确定性行业均值”
"""

        try:
            text = self._llm_to_str(self.llm.invoke(prompt)).strip()
            status = "ok" if scorecard.get("decision") == "proceed" else "proceed_with_caveat"
            return {"answer": text, "status": status, "scorecard": scorecard}
        except Exception as e:
            return {
                "answer": search_evidence or sql_evidence,
                "status": f"fallback:{e}",
                "scorecard": scorecard
            }
