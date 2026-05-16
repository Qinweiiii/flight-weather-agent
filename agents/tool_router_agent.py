"""
Tool Router Agent

根据用户问题和意图，动态决定工具执行链路。
"""

from typing import Dict, Any, List


class ToolRouterAgent:
    """动态工具路由智能体"""

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

    def route(self, question: str, intent: str, available_tools: List[str]) -> Dict[str, Any]:
        """返回执行路径。

        默认与 intent 保持一致，避免与意图识别重复决策。
        仅在 intent 缺失时尝试用 LLM 兜底一次。
        """
        valid = {
            "simple_answer",
            "sql_only",
            "analysis_only",
            "sql_and_analysis",
            "web_search",
            "search_and_sql",
        }

        intent_norm = (intent or "").strip().lower()
        if intent_norm in valid:
            return {"path": intent_norm, "reason": "dedup_intent_direct"}

        prompt = f"""你是工具路由智能体。

用户问题：{question}
当前意图为空或无效：{intent}
可用工具：{', '.join(available_tools)}

请只返回一个执行路径（path），可选值：
- simple_answer
- sql_only
- analysis_only
- sql_and_analysis
- web_search
- search_and_sql

只返回 path 本身，不要解释。
"""

        try:
            raw = self._llm_to_str(self.llm.invoke(prompt)).strip().lower()
            for item in valid:
                if item in raw:
                    return {"path": item, "reason": "llm_router_fallback"}
        except Exception:
            pass

        return {"path": "sql_only", "reason": "fallback_sql_only"}
