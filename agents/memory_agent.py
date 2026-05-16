"""
Memory Agent

封装长期记忆检索与上下文注入策略。
"""

from typing import Dict, Any, List

from memory.long_term_memory import LongTermMemory


class MemoryAgent:
    """记忆检索与注入智能体"""

    def __init__(self, long_term_memory: LongTermMemory):
        self.long_term_memory = long_term_memory

    def build_user_context(self, user_id: str, question: str, top_k: int = 3) -> Dict[str, Any]:
        """召回用户相关记忆，返回结构化上下文。"""
        if not user_id:
            return {"preferences": {}, "knowledge": [], "context_text": ""}

        preferences = self.long_term_memory.get_all_preferences(user_id)
        knowledge = self.long_term_memory.get_relevant_knowledge(user_id, question, top_k=top_k)

        parts: List[str] = []
        if preferences:
            pref_lines = [f"- {k}: {v}" for k, v in preferences.items()]
            parts.append("用户偏好：\n" + "\n".join(pref_lines))
        if knowledge:
            know_lines = [f"- {item.get('content', '')}" for item in knowledge]
            parts.append("相关背景：\n" + "\n".join(know_lines))

        return {
            "preferences": preferences,
            "knowledge": knowledge,
            "context_text": "\n\n".join(parts) if parts else "",
        }
