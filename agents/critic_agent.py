"""
Critic Agent

对主回答进行结构化质量评估，并返回改写版答案。
"""

import json
from typing import Dict, Any, Optional


class CriticAgent:
    """回答审校智能体"""

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
    def _clamp_score(value: Any, default: int = 3) -> int:
        """将评分限制在 1-5 区间，避免异常值污染总分。"""
        try:
            score = int(value)
        except Exception:
            score = default
        return max(1, min(5, score))

    def critique_and_refine(
        self,
        question: str,
        draft_answer: str,
        sql_result: Optional[str] = None,
        search_result: Optional[str] = None,
    ) -> Dict[str, Any]:
        """按结构化Rubric审校草稿并返回优化后的答案。"""
        prompt = f"""你是审校智能体，请按评分标准检查回答质量并给出可直接展示的最终回答。

用户问题：{question}
草稿回答：{draft_answer}
SQL结果：{sql_result or '无'}
搜索结果：{search_result or '无'}

请只返回一个 JSON 对象，不要代码块、不要解释，格式如下：
{{
  "status": "pass|revise",
  "rubric": {{
    "groundedness": 1-5,
    "completeness": 1-5,
    "clarity": 1-5,
    "actionability": 1-5
  }},
  "issues": ["问题1", "问题2"],
  "answer": "最终可展示给用户的回答"
}}

要求：
- 不能编造 SQL/搜索中不存在的数据
- 如果存在时间口径不可比，必须在回答中提示
- 保留原回答重点，尽量少改动
- 若总分 >= 16 倾向 pass，否则 revise
"""

        try:
            raw = self._llm_to_str(self.llm.invoke(prompt))
            payload = raw.strip()
            if payload.startswith("```"):
                payload = payload.strip("`")
                if payload.startswith("json"):
                    payload = payload[4:]
            payload = payload.strip()

            data = json.loads(payload)
            rubric = data.get("rubric", {}) if isinstance(data, dict) else {}

            normalized_rubric = {
                "groundedness": self._clamp_score(rubric.get("groundedness", 3)),
                "completeness": self._clamp_score(rubric.get("completeness", 3)),
                "clarity": self._clamp_score(rubric.get("clarity", 3)),
                "actionability": self._clamp_score(rubric.get("actionability", 3)),
            }
            total_score = sum(normalized_rubric.values())

            status_raw = str(data.get("status", "")).lower()
            status = "pass" if ("pass" in status_raw or total_score >= 16) else "revise"

            issues = data.get("issues", [])
            if not isinstance(issues, list):
                issues = [str(issues)]

            answer = str(data.get("answer", "")).strip() or draft_answer

            return {
                "status": status,
                "answer": answer,
                "rubric": normalized_rubric,
                "total_score": total_score,
                "issues": [str(x) for x in issues if str(x).strip()]
            }
        except Exception:
            # 兼容旧格式输出：首行 PASS/REVISE + 回答正文
            try:
                raw = self._llm_to_str(self.llm.invoke(
                    f"请审校以下回答。第一行只输出 PASS 或 REVISE，后面输出最终回答。\n用户问题：{question}\n草稿回答：{draft_answer}"
                ))
                lines = [line for line in raw.splitlines() if line.strip()]
                first = lines[0].strip().upper() if lines else "PASS"
                status = "revise" if "REVISE" in first else "pass"
                answer = "\n".join(lines[1:]).strip() if len(lines) > 1 else draft_answer
                return {
                    "status": status,
                    "answer": answer or draft_answer,
                    "rubric": {
                        "groundedness": 3,
                        "completeness": 3,
                        "clarity": 3,
                        "actionability": 3,
                    },
                    "total_score": 12,
                    "issues": []
                }
            except Exception:
                return {
                    "status": "pass",
                    "answer": draft_answer,
                    "rubric": {
                        "groundedness": 3,
                        "completeness": 3,
                        "clarity": 3,
                        "actionability": 3,
                    },
                    "total_score": 12,
                    "issues": ["critic_fallback"]
                }
