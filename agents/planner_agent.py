"""
Planner Agent

将复杂用户问题拆分为可执行步骤，供主智能体进行多阶段调度。
"""

import json
from typing import Dict, Any, List


class PlannerAgent:
    """任务规划智能体"""

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

    def plan(self, question: str) -> Dict[str, Any]:
        """生成结构化执行计划。"""
        prompt = f"""你是任务规划智能体。请将用户问题拆分为执行步骤，输出JSON。

用户问题：{question}

返回格式：
{{
  "goal": "总体目标",
  "steps": [
    {{"id": 1, "agent": "sql|analysis|search|memory|summary", "task": "要做什么"}}
  ],
  "parallel_groups": [[1,2]],
  "notes": "关键注意事项"
}}

要求：
1. steps 数量控制在 1-5 步
2. 只有在步骤互不依赖时，才放入 parallel_groups
3. 只返回 JSON，不要其他文字
"""

        try:
            response = self._llm_to_str(self.llm.invoke(prompt)).strip()
            if response.startswith("```json"):
                response = response[7:]
            elif response.startswith("```"):
                response = response[3:]
            if response.endswith("```"):
                response = response[:-3]
            response = response.strip()
            plan = json.loads(response)
            if isinstance(plan, dict) and isinstance(plan.get("steps"), list):
                return plan
        except Exception:
            pass

        # 保底计划
        return {
            "goal": question,
            "steps": [{"id": 1, "agent": "summary", "task": "直接回答用户问题"}],
            "parallel_groups": [],
            "notes": "fallback_plan"
        }
