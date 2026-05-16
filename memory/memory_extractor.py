"""
记忆提取器

从对话历史中自动提取用户偏好和知识。
"""

import json
from typing import List, Dict, Any
from langchain.messages import HumanMessage, AIMessage
from langchain_core.messages import BaseMessage
from langchain_core.language_models import BaseLLM


class MemoryExtractor:
    """从对话中提取长期记忆"""
    
    def __init__(self, llm: BaseLLM):
        """初始化记忆提取器
        
        Args:
            llm: 语言模型实例
        """
        self.llm = llm
    
    @staticmethod
    def _llm_to_str(result) -> str:
        """安全地从 LLM 返回值中提取文本，清理思考标签"""
        import re
        if isinstance(result, str):
            text = result
        elif hasattr(result, 'content'):
            text = str(result.content)
        elif hasattr(result, 'text'):
            text = str(result.text)
        else:
            text = str(result)
        text = re.sub(r'<think>[\s\S]*?</think>', '', text).strip()
        text = re.sub(r'</think>', '', text).strip()
        return text
    
    def extract_preferences_from_conversation(
        self, 
        messages: List[BaseMessage], 
        user_id: str
    ) -> Dict[str, Any]:
        """从对话中提取用户偏好
        
        Args:
            messages: 对话消息列表
            user_id: 用户ID
            
        Returns:
            提取的偏好字典 {category: {key: value, ...}}
        """
        if len(messages) < 4:  # 至少需要2轮对话
            return {}
        
        # 格式化对话历史
        conversation_text = self._format_conversation(messages)
        
        prompt = f"""请分析以下对话，提取用户在“航班-天气分析”场景下的偏好信息。

对话内容：
{conversation_text}

请优先提取以下类型的用户偏好（能提取则返回，不能提取可省略）：
1. favorite_metric: 用户最关注的指标（如：到达延误率、取消率、改降率、航班量）
2. focus_dimension: 用户最常按什么维度分析（如：航司、机场、航线、时间窗、天气条件）
3. period_preference: 用户常用的时间口径（如：近30天、近90天、按月）
4. display_preference: 用户偏好的展示方式（如：TopN列表、趋势图、对比表）
5. common_topics: 用户常问的话题

以JSON格式返回（只返回JSON，不要其他文字）：
{{
    "favorite_metric": "到达延误率",
    "focus_dimension": "按航司对比",
    "period_preference": "近90天",
    ...
}}

如果无法提取某项偏好，则不要包含该键。
"""
        
        try:
            response = self._llm_to_str(self.llm.invoke(prompt)).strip()
            
            # 清理可能的代码块标记
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                response = response.split("```")[1].split("```")[0].strip()
            
            preferences = json.loads(response)
            return preferences
        except Exception as e:
            print(f"提取偏好失败: {e}")
            return {}
    
    def extract_knowledge_from_conversation(
        self, 
        messages: List[BaseMessage], 
        user_id: str
    ) -> List[Dict[str, Any]]:
        """从对话中提取用户知识点
        
        Args:
            messages: 对话消息列表
            user_id: 用户ID
            
        Returns:
            提取的知识列表 [{category, content, confidence}, ...]
        """
        if len(messages) < 4:
            return []
        
        conversation_text = self._format_conversation(messages)
        
        prompt = f"""请分析以下对话，提取值得记住的用户知识点（面向航班-天气分析场景）。

对话内容：
{conversation_text}

请优先提取以下类型的知识：
1. 常问问题：用户反复询问的问题或关注点（如延误、取消、天气影响）
2. 分析口径偏好：用户偏好的时间窗、分组维度、指标定义
3. 使用习惯：用户的提问与分析习惯（如偏好 TopN、要求给建议、偏好对比分析）

以JSON数组格式返回（只返回JSON，不要其他文字）：
[
    {{
        "category": "常问问题",
        "content": "经常询问近90天各航司到达延误率及优化建议",
        "confidence": 0.9
    }},
    ...
]

confidence是置信度（0-1），根据对话中该知识的明确程度评估。
如果没有值得记录的知识，返回空数组 []。
"""
        
        try:
            response = self._llm_to_str(self.llm.invoke(prompt)).strip()
            
            # 清理可能的代码块标记
            if "```json" in response:
                response = response.split("```json")[1].split("```")[0].strip()
            elif "```" in response:
                response = response.split("```")[1].split("```")[0].strip()
            
            knowledge_list = json.loads(response)
            
            # 验证格式
            if isinstance(knowledge_list, list):
                return knowledge_list
            return []
        except Exception as e:
            print(f"提取知识失败: {e}")
            return []
    
    def _format_conversation(self, messages: List[BaseMessage]) -> str:
        """格式化对话历史为文本
        
        Args:
            messages: 消息列表
            
        Returns:
            格式化的对话文本
        """
        lines = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                lines.append(f"用户: {msg.content}")
            elif isinstance(msg, AIMessage):
                lines.append(f"助手: {msg.content}")
        
        return "\n".join(lines)
    
    def should_extract(self, messages: List[BaseMessage], threshold: int = 6) -> bool:
        """判断是否应该提取记忆
        
        Args:
            messages: 消息列表
            threshold: 消息数量阈值
            
        Returns:
            是否应该提取
        """
        # 至少需要一定数量的对话才提取
        return len(messages) >= threshold

