"""
主智能体

负责意图识别、任务路由、协调子智能体和结果汇总。
支持6种意图：simple_answer / sql_only / analysis_only / sql_and_analysis / web_search / search_and_sql
"""

import json
import time
import uuid
import sqlite3
import concurrent.futures
from typing import TypedDict, Sequence, Dict, Any, Optional, Annotated, Generator
from pathlib import Path

from langgraph.graph import StateGraph, END, add_messages
from langgraph.checkpoint.memory import MemorySaver
from langchain.messages import HumanMessage, AIMessage
from langchain_core.messages import BaseMessage
from langchain_core.language_models import BaseLLM

import sys
sys.path.append(str(Path(__file__).parent.parent))
from prompts import get_master_intent_prompt, get_summary_prompt
from agents.sql_agent import SQLQueryAgent
from agents.analysis_agent import DataAnalysisAgent
from agents.search_agent import WebSearchAgent
from agents.planner_agent import PlannerAgent
from agents.tool_router_agent import ToolRouterAgent
from agents.critic_agent import CriticAgent
from agents.guardrail_agent import GuardrailAgent
from agents.memory_agent import MemoryAgent
from agents.debate_agent import DebateAgent
from memory.long_term_memory import LongTermMemory
from memory.memory_extractor import MemoryExtractor


class MasterAgentState(TypedDict):
    """主智能体状态定义"""
    messages: Annotated[Sequence[BaseMessage], add_messages]
    user_question: str
    intent: Optional[str]
    sql_result: Optional[Dict[str, Any]]
    analysis_result: Optional[Dict[str, Any]]
    search_result: Optional[Dict[str, Any]]
    final_answer: Optional[str]
    error: Optional[str]
    metadata: Dict[str, Any]


class MasterAgent:
    """主智能体 - 协调SQL查询和数据分析子智能体"""
    
    @staticmethod
    def _llm_to_str(result) -> str:
        """安全地从 LLM 返回值中提取文本字符串
        
        兼容 str / AIMessage / GenerationChunk 等多种返回类型。
        自动清理思考型模型（如 qwen3.5-plus）的 <think>...</think> 标签。
        """
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
    
    def __init__(self, llm: BaseLLM, db_path: str, num_examples: int = 3, 
                memory_db_path: str = "./data/long_term_memory.db",
                short_term_max_tokens: int = 1000,
                tavily_api_key: str = ""):
        """初始化主智能体
        
        Args:
            llm: 语言模型实例
            db_path: 数据库路径
            num_examples: Few-shot示例数量
            memory_db_path: 长期记忆数据库路径
            short_term_max_tokens: 短期记忆最大token数
            tavily_api_key: Tavily 搜索 API Key
        """
        self.llm = llm
        self.db_path = db_path
        self.short_term_max_tokens = short_term_max_tokens
        self._data_period_cache: Optional[Dict[str, str]] = None
        
        # 初始化子智能体
        self.sql_agent = SQLQueryAgent(llm, db_path, num_examples)
        self.analysis_agent = DataAnalysisAgent(llm)
        self.search_agent = WebSearchAgent(llm, tavily_api_key=tavily_api_key)
        
        # 初始化短期记忆（MemorySaver）
        self.memory = MemorySaver()
        
        # 初始化长期记忆（LongTermMemory）
        self.long_term_memory = LongTermMemory(memory_db_path)
        self.memory_agent = MemoryAgent(self.long_term_memory)
        
        # 初始化记忆提取器
        self.memory_extractor = MemoryExtractor(llm)

        # 新增协作智能体
        self.planner_agent = PlannerAgent(llm)
        self.tool_router_agent = ToolRouterAgent(llm)
        self.critic_agent = CriticAgent(llm)
        self.guardrail_agent = GuardrailAgent(llm)
        self.debate_agent = DebateAgent(llm)
        
        # 会话数据存储：保存每个thread_id的最近查询结果
        self.session_data = {}
        
        # 构建工作流
        self.graph = self._build_graph()

    def _get_internal_data_period(self) -> Dict[str, Optional[str]]:
        """读取内部数据库可用时间范围，用于在回答中标注数据观测期。"""
        if self._data_period_cache is not None:
            return self._data_period_cache

        period = {"min_ts": None, "max_ts": None, "source": None}
        candidates = [
            ("flights_enriched", "FL_DATE"),
            ("v_flight_monthly_kpi", "ym")
        ]

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            for table, col in candidates:
                try:
                    cursor.execute(
                        f"SELECT MIN({col}) AS min_ts, MAX({col}) AS max_ts "
                        f"FROM {table} WHERE {col} IS NOT NULL"
                    )
                    row = cursor.fetchone()
                    if not row:
                        continue
                    min_ts, max_ts = row
                    if not max_ts:
                        continue

                    if period["max_ts"] is None or str(max_ts) > str(period["max_ts"]):
                        period["max_ts"] = str(max_ts)
                        period["min_ts"] = str(min_ts) if min_ts else None
                        period["source"] = f"{table}.{col}"
                except Exception:
                    continue
        finally:
            conn.close()

        self._data_period_cache = period
        return period

    def _append_period_notice(self, answer: str, intent: str, sql_result: Optional[Dict[str, Any]]) -> str:
        """为 SQL 相关回答追加观测期和时间口径提示，避免跨年份误读。"""
        if not answer:
            return answer

        if intent not in ("sql_only", "sql_and_analysis", "search_and_sql"):
            return answer

        if not sql_result or sql_result.get("error"):
            return answer

        period = self._get_internal_data_period()
        min_ts = period.get("min_ts")
        max_ts = period.get("max_ts")
        if not min_ts or not max_ts:
            return answer

        min_day = str(min_ts)[:10]
        max_day = str(max_ts)[:10]

        period_note = (
            f"\n\n---\n"
            f"数据观测期：内部数据库时间范围为 {min_day} 至 {max_day}。"
        )

        if intent == "search_and_sql":
            period_note += (
                "\n口径提示：若外部搜索使用的是 2026 年等最新市场数据，"
                "与该历史区间不可直接做绝对值横向比较，建议优先采用趋势或同口径同比对比。"
            )

        # 避免重复追加
        if "数据观测期：" in answer:
            return answer

        return answer + period_note

    def _compute_confidence_label(self, search_result: Optional[Dict[str, Any]]) -> str:
        """基于 search/debate 质量元数据给出高/中/低可信度标签。"""
        if not search_result or not isinstance(search_result, dict):
            return "中"

        quality = search_result.get("quality", {}) if isinstance(search_result.get("quality"), dict) else {}
        debate = search_result.get("debate", {}) if isinstance(search_result.get("debate"), dict) else {}
        scorecard = debate.get("scorecard", {}) if isinstance(debate.get("scorecard"), dict) else {}

        evidence_enough = quality.get("evidence_enough", True)
        status = str(debate.get("status", "")).lower()
        total = scorecard.get("total")
        threshold = scorecard.get("threshold", 13)

        if not evidence_enough or status in ("low_evidence", "skipped_low_evidence"):
            return "低"

        if isinstance(total, (int, float)):
            if total >= max(16, threshold + 3):
                return "高"
            if total >= threshold:
                return "中"
            return "低"

        return "中"

    def _prepend_confidence_banner(self, answer: str, label: str) -> str:
        """在回答顶部增加可信度提示，避免重复添加。"""
        if not answer:
            return answer
        if "本次结论可信度：" in answer:
            return answer
        return f"本次结论可信度：{label}\n\n{answer}"

    def _append_trace(self, state: MasterAgentState, step: str, detail: str):
        """记录轻量执行轨迹，供调试与前端展示。"""
        metadata = state.setdefault("metadata", {})
        trace = metadata.setdefault("trace", [])
        trace.append({
            "ts": int(time.time() * 1000),
            "step": step,
            "detail": detail
        })

    def _critique_answer(
        self,
        question: str,
        draft_answer: str,
        sql_result: Optional[Dict[str, Any]] = None,
        search_result: Optional[Dict[str, Any]] = None
    ) -> str:
        """调用 CriticAgent 审校并轻量润色回答。"""
        if not draft_answer:
            return draft_answer

        sql_evidence = None
        if sql_result:
            sql_evidence = sql_result.get("data") or sql_result.get("error")

        search_evidence = None
        if search_result:
            search_evidence = search_result.get("answer") or search_result.get("error")

        reviewed = self.critic_agent.critique_and_refine(
            question=question,
            draft_answer=draft_answer,
            sql_result=sql_evidence,
            search_result=search_evidence
        )
        return reviewed.get("answer", draft_answer)
    
    def _build_graph(self) -> StateGraph:
        """构建LangGraph状态图（支持6种意图路由）"""
        workflow = StateGraph(MasterAgentState)
        
        # 添加节点
        workflow.add_node("intent", self._intent_node)
        workflow.add_node("simple_answer", self._simple_answer_node)
        workflow.add_node("call_sql", self._call_sql_node)
        workflow.add_node("call_analysis", self._call_analysis_node)
        workflow.add_node("call_both", self._call_both_node)
        workflow.add_node("call_web_search", self._call_web_search_node)
        workflow.add_node("call_search_and_sql", self._call_search_and_sql_node)
        workflow.add_node("summarize", self._summarize_node)
        
        # 设置入口
        workflow.set_entry_point("intent")
        
        # 添加条件边 - 从意图识别到不同的处理节点（6种意图）
        workflow.add_conditional_edges(
            "intent",
            self._route_after_intent,
            {
                "simple_answer": "simple_answer",
                "sql_only": "call_sql",
                "analysis_only": "call_analysis",
                "sql_and_analysis": "call_both",
                "web_search": "call_web_search",
                "search_and_sql": "call_search_and_sql"
            }
        )
        
        # 添加边
        workflow.add_edge("simple_answer", END)
        workflow.add_edge("call_sql", "summarize")
        workflow.add_edge("call_analysis", "summarize")
        workflow.add_edge("call_both", "summarize")
        workflow.add_edge("call_web_search", "summarize")
        workflow.add_edge("call_search_and_sql", "summarize")
        workflow.add_edge("summarize", END)
        
        # 使用MemorySaver作为checkpointer
        return workflow.compile(checkpointer=self.memory)
    
    def _get_conversation_history(self, state: MasterAgentState) -> str:
        """获取对话历史摘要（智能压缩版本）
        
        策略：
        1. 如果消息少于等于10条，直接返回所有
        2. 如果消息较多但token未超限，返回近期消息
        3. 如果消息很多且超过token限制，使用LLM总结压缩
        
        Args:
            state: 当前状态
            
        Returns:
            对话历史摘要
        """
        messages = state.get("messages", [])
        if len(messages) <= 1:
            return ""
        
        # 构建原始历史（排除当前消息）
        history_text = self._format_messages(messages[:-1])
        
        # 如果消息数量少，直接返回
        if len(messages) <= 11:  # 10条历史消息
            return history_text
        
        # 简单token估算（中文按2字符=1token，英文按4字符=1token）
        estimated_tokens = len(history_text) / 2.5
        
        if estimated_tokens <= self.short_term_max_tokens:
            return history_text
        
        # 需要压缩：使用LLM总结
        return self._compress_history_with_llm(history_text)
    
    def _format_messages(self, messages: Sequence[BaseMessage]) -> str:
        """格式化消息列表为文本
        
        Args:
            messages: 消息列表
            
        Returns:
            格式化的文本
        """
        history = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                history.append(f"用户: {msg.content}")
            elif isinstance(msg, AIMessage):
                history.append(f"助手: {msg.content}")
        
        return "\n".join(history) if history else ""
    
    def _compress_history_with_llm(self, history_text: str) -> str:
        """使用LLM总结压缩对话历史
        
        Args:
            history_text: 原始对话历史文本
            
        Returns:
            压缩后的摘要文本
        """
        prompt = f"""请总结以下对话历史，保留关键信息、用户偏好和重要上下文：

{history_text}

总结要求：
1. 保留关键事实和数据（如查询的部门、员工、数据结果）
2. 提取用户关注点和偏好
3. 保留重要的上下文信息
4. 简洁但信息完整
5. 不超过300字

总结："""
        
        try:
            summary = self._llm_to_str(self.llm.invoke(prompt)).strip()
            return f"[对话历史总结]\n{summary}"
        except Exception as e:
            print(f"压缩对话历史失败: {e}")
            # 如果压缩失败，返回最近的部分对话
            lines = history_text.split("\n")
            recent_lines = lines[-20:] if len(lines) > 20 else lines
            return "\n".join(recent_lines)
    
    def _format_long_term_context(
        self, 
        knowledge: list, 
        preferences: Dict[str, str]
    ) -> str:
        """格式化长期记忆上下文
        
        Args:
            knowledge: 用户知识列表
            preferences: 用户偏好字典
            
        Returns:
            格式化的上下文文本
        """
        context_parts = []
        
        # 添加用户偏好
        if preferences:
            pref_lines = [f"- {key}: {value}" for key, value in preferences.items()]
            context_parts.append("用户偏好：\n" + "\n".join(pref_lines))
        
        # 添加相关知识
        if knowledge:
            know_lines = [f"- {item['content']}" for item in knowledge[:3]]
            context_parts.append("相关背景：\n" + "\n".join(know_lines))
        
        return "\n\n".join(context_parts) if context_parts else ""
    
    def _intent_node(self, state: MasterAgentState) -> MasterAgentState:
        """意图识别节点（支持6种意图）"""
        question = state["user_question"]
        user_id = state["metadata"].get("user_id")
        
        # 获取对话历史（短期记忆）
        conversation_history = self._get_conversation_history(state)
        
        # 获取用户知识（长期记忆）
        user_context = ""
        if user_id:
            try:
                memory_ctx = self.memory_agent.build_user_context(user_id, question, top_k=3)
                user_context = memory_ctx.get("context_text", "")
            except Exception as e:
                print(f"获取长期记忆失败: {e}")
        
        prompt = get_master_intent_prompt(question, conversation_history, user_context)
        
        try:
            response = self._llm_to_str(self.llm.invoke(prompt)).strip()
            intent = response.lower().strip()
            
            valid_intents = [
                "simple_answer", "sql_only", "analysis_only",
                "sql_and_analysis", "web_search", "search_and_sql"
            ]
            if intent not in valid_intents:
                for valid_intent in valid_intents:
                    if valid_intent in intent:
                        intent = valid_intent
                        break
                else:
                    intent = "sql_only"
            
            state["intent"] = intent
            state["metadata"]["intent_response"] = response
            self._append_trace(state, "intent", intent)
            
        except Exception as e:
            state["error"] = f"意图识别失败: {str(e)}"
            state["intent"] = "simple_answer"
        
        return state
    
    def _route_after_intent(self, state: MasterAgentState) -> str:
        """意图识别后的路由（支持6种意图）"""
        intent = state.get("intent", "simple_answer")
        router_mode = state.get("metadata", {}).get("router_mode", "direct")

        if router_mode == "enhanced":
            try:
                route = self.tool_router_agent.route(
                    question=state.get("user_question", ""),
                    intent=intent,
                    available_tools=["sql", "analysis", "search", "memory", "summary"]
                )
                route_path = route.get("path", intent)
                self._append_trace(state, "tool_route", f"{route_path} ({route.get('reason', 'unknown')})")
                intent = route_path
            except Exception as e:
                self._append_trace(state, "tool_route", f"fallback:{e}")
        else:
            self._append_trace(state, "tool_route", f"{intent} (intent_direct)")

        # 如果 web_search/search_and_sql 但搜索不可用，降级为 simple_answer
        if intent in ("web_search", "search_and_sql") and not self.search_agent.available:
            print("[路由] 搜索智能体不可用，意图降级为 simple_answer")
            state["final_answer"] = (
                "联网搜索功能暂未启用。请配置 TAVILY_API_KEY 环境变量后重启系统。\n"
                "申请地址：https://tavily.com（免费账户即可）"
            )
            return "simple_answer"
        return intent
    
    def _simple_answer_node(self, state: MasterAgentState) -> MasterAgentState:
        """简单回答节点"""
        question = state["user_question"]
        
        common_responses = {
            "你好": "你好！我是航班-天气分析助手，可以帮你查询航班延误、取消、改降、机场表现和天气影响等信息。有什么可以帮你的吗？",
            "谢谢": "不客气！还有什么其他问题吗？",
            "再见": "再见！祝你工作顺利！",
            "帮助": "我可以帮你：\n1. 查询航班数据（如：最近30天航班量、延误率Top机场）\n2. 分析运营指标（如：到达延误率、取消率、改降率）\n3. 综合查询与分析（如：天气因素对延误的影响并给出优化建议）",
        }
        
        # 检查常见问候
        answer = None
        for key, response in common_responses.items():
            if key in question:
                answer = response
                break
        
        # 默认回复
        if not answer:
            answer = "我是航班-天气分析助手。请问你想看航班延误、取消、机场表现，还是天气影响相关的数据？"
        
        state["final_answer"] = answer
        
        # 将AI回答添加到messages中
        state["messages"] = state["messages"] + [AIMessage(content=answer)]
        
        return state
    
    def _call_sql_node(self, state: MasterAgentState) -> MasterAgentState:
        """调用SQL查询子智能体"""
        question = state["user_question"]
        thread_id = state["metadata"].get("thread_id", "default")
        
        try:
            t0 = time.time()
            result = self.sql_agent.query(question)
            state["sql_result"] = result
            state["metadata"]["sql_result"] = result
            self._append_trace(
                state,
                "call_sql",
                f"latency_ms={int((time.time() - t0) * 1000)}, retry={result.get('retry_count', 0)}, failure_stage={result.get('failure_stage')}"
            )
            
            # 保存到会话数据存储
            if thread_id not in self.session_data:
                self.session_data[thread_id] = {}
            self.session_data[thread_id]["last_sql_result"] = result
            
        except Exception as e:
            state["error"] = f"SQL查询失败: {str(e)}"
            state["sql_result"] = {"error": str(e)}
        
        return state
    
    def _call_analysis_node(self, state: MasterAgentState) -> MasterAgentState:
        """调用数据分析子智能体"""
        question = state["user_question"]
        thread_id = state["metadata"].get("thread_id", "default")
        
        # 从会话数据存储中获取最近的查询结果
        data_to_analyze = None
        
        # 首先检查当前state中是否有查询结果
        if state.get("sql_result") and "data" in state["sql_result"]:
            data_to_analyze = state["sql_result"]["data"]
        # 否则从会话数据存储中获取历史查询结果
        elif thread_id in self.session_data and "last_sql_result" in self.session_data[thread_id]:
            last_sql_result = self.session_data[thread_id]["last_sql_result"]
            if last_sql_result and "data" in last_sql_result:
                data_to_analyze = last_sql_result["data"]
        
        if not data_to_analyze:
            state["error"] = "没有找到可以分析的数据。请先进行数据查询。"
            state["analysis_result"] = {"error": "无可用数据"}
            return state
        
        try:
            t0 = time.time()
            result = self.analysis_agent.analyze(data_to_analyze, question)
            state["analysis_result"] = result
            state["metadata"]["analysis_result"] = result
            self._append_trace(
                state,
                "call_analysis",
                f"latency_ms={int((time.time() - t0) * 1000)}, has_error={bool(result.get('error'))}"
            )
        except Exception as e:
            state["error"] = f"数据分析失败: {str(e)}"
            state["analysis_result"] = {"error": str(e)}
        
        return state
    
    def _call_both_node(self, state: MasterAgentState) -> MasterAgentState:
        """先调用SQL查询，再调用数据分析"""
        question = state["user_question"]
        thread_id = state["metadata"].get("thread_id", "default")
        
        try:
            sql_result = self.sql_agent.query(question)
            state["sql_result"] = sql_result
            state["metadata"]["sql_result"] = sql_result
            
            if thread_id not in self.session_data:
                self.session_data[thread_id] = {}
            self.session_data[thread_id]["last_sql_result"] = sql_result
            
            if sql_result.get("error"):
                state["error"] = f"SQL查询失败: {sql_result['error']}"
                return state
            
            if sql_result.get("data"):
                analysis_result = self.analysis_agent.analyze(sql_result["data"], question)
                state["analysis_result"] = analysis_result
                state["metadata"]["analysis_result"] = analysis_result
                
                if analysis_result.get("error"):
                    state["error"] = f"数据分析失败: {analysis_result['error']}"
            else:
                state["error"] = "查询结果为空，无法进行分析"
                
        except Exception as e:
            state["error"] = f"执行失败: {str(e)}"
        
        return state
    
    def _call_web_search_node(self, state: MasterAgentState) -> MasterAgentState:
        """联网搜索节点（纯搜索模式）"""
        question = state["user_question"]
        
        try:
            t0 = time.time()
            search_result = self.search_agent.search(question)
            state["search_result"] = search_result
            state["metadata"]["search_result"] = search_result
            self._append_trace(
                state,
                "call_web_search",
                f"latency_ms={int((time.time() - t0) * 1000)}, has_error={bool(search_result.get('error'))}"
            )
            
            if search_result.get("error"):
                state["error"] = search_result["error"]
                
        except Exception as e:
            state["error"] = f"联网搜索失败: {str(e)}"
        
        return state
    
    def _call_search_and_sql_node(self, state: MasterAgentState) -> MasterAgentState:
        """联网搜索 + 数据库查询联合分析节点"""
        question = state["user_question"]
        thread_id = state["metadata"].get("thread_id", "default")
        
        try:
            t0 = time.time()
            # SQL 与搜索并行执行，再交给 DebateAgent 裁决
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                fut_sql = pool.submit(self.sql_agent.query, question)
                fut_search = pool.submit(self.search_agent.search, question)

                sql_result = fut_sql.result()
                base_search_result = fut_search.result()

            state["sql_result"] = sql_result
            state["metadata"]["sql_result"] = sql_result
            
            if thread_id not in self.session_data:
                self.session_data[thread_id] = {}
            self.session_data[thread_id]["last_sql_result"] = sql_result

            quality = base_search_result.get("quality", {}) if isinstance(base_search_result, dict) else {}
            sql_evidence = sql_result.get("data") or sql_result.get("error") or "无SQL证据"
            search_evidence = base_search_result.get("answer") or base_search_result.get("error") or "无搜索证据"

            debate_result = self.debate_agent.adjudicate(question, str(sql_evidence), str(search_evidence))
            search_result = {
                "answer": debate_result.get("answer", ""),
                "sources": base_search_result.get("sources", []),
                "error": None,
                "quality": quality,
                "debate": {
                    "status": debate_result.get("status", "unknown"),
                    "scorecard": debate_result.get("scorecard", {})
                }
            }

            if debate_result.get("status") == "low_evidence":
                self._append_trace(state, "debate", "low_evidence_threshold_triggered")
            else:
                self._append_trace(state, "debate", "search_and_sql_adjudicated")

            if base_search_result.get("error") and sql_result.get("error"):
                search_result["error"] = f"搜索与SQL均失败: {base_search_result.get('error')} | {sql_result.get('error')}"

            state["search_result"] = search_result
            state["metadata"]["search_result"] = search_result
            self._append_trace(
                state,
                "call_search_and_sql",
                f"latency_ms={int((time.time() - t0) * 1000)}, sql_failure_stage={sql_result.get('failure_stage')}"
            )
            
            if search_result.get("error") and not sql_result.get("error"):
                state["error"] = search_result["error"]
                
        except Exception as e:
            state["error"] = f"联合分析失败: {str(e)}"
        
        return state
    
    def _summarize_node(self, state: MasterAgentState) -> MasterAgentState:
        """汇总结果节点（支持搜索结果和图表元数据）"""
        question = state["user_question"]
        intent = state.get("intent", "sql_only")
        
        # 预设回答已经生成（如降级处理）
        if state.get("final_answer"):
            state["final_answer"] = self._critique_answer(
                question,
                state["final_answer"],
                state.get("sql_result"),
                state.get("search_result")
            )
            state["final_answer"] = self._append_period_notice(
                state["final_answer"],
                intent,
                state.get("sql_result")
            )
            state["messages"] = list(state["messages"]) + [AIMessage(content=state["final_answer"])]
            return state
        
        if state.get("error"):
            state["final_answer"] = f"抱歉，处理过程中出现错误：{state['error']}"
            state["messages"] = list(state["messages"]) + [AIMessage(content=state["final_answer"])]
            return state
        
        sql_result = state.get("sql_result")
        analysis_result = state.get("analysis_result")
        search_result = state.get("search_result")
        
        # 联网搜索相关意图：搜索智能体已生成完整回答
        if intent in ("web_search", "search_and_sql") and search_result:
            if search_result.get("error"):
                state["final_answer"] = f"搜索出错：{search_result['error']}"
            else:
                answer = search_result.get("answer", "未能获取搜索结果")
                sources = search_result.get("sources", [])
                if sources:
                    sources_text = "\n\n**参考来源：**\n" + "\n".join(
                        f"- {url}" for url in sources[:5]
                    )
                    answer = answer + sources_text
                confidence_label = self._compute_confidence_label(search_result)
                answer = self._prepend_confidence_banner(answer, confidence_label)
                state["final_answer"] = self._critique_answer(
                    question,
                    answer,
                    sql_result,
                    search_result
                )
                state["final_answer"] = self._append_period_notice(
                    state["final_answer"],
                    intent,
                    sql_result
                )
                # 将图表元数据附加在 metadata 中供前端使用
                if search_result.get("chart"):
                    state["metadata"]["chart"] = search_result["chart"]
            state["messages"] = list(state["messages"]) + [AIMessage(content=state["final_answer"])]
            return state
        
        # 数据库查询/分析相关意图
        sql_data = None
        analysis_data = None
        
        if sql_result:
            if sql_result.get("error"):
                state["final_answer"] = f"查询出错：{sql_result['error']}"
                state["messages"] = list(state["messages"]) + [AIMessage(content=state["final_answer"])]
                return state
            sql_data = sql_result.get("data")
        
        if analysis_result:
            if analysis_result.get("error"):
                state["final_answer"] = f"分析出错：{analysis_result['error']}"
                state["messages"] = list(state["messages"]) + [AIMessage(content=state["final_answer"])]
                return state
            analysis_data = analysis_result.get("analysis")
            # 将图表配置存入 metadata，流式接口和前端可读取
            if analysis_result.get("chart"):
                state["metadata"]["chart"] = analysis_result["chart"]
        
        try:
            prompt = get_summary_prompt(
                question=question,
                sql_result=sql_data,
                analysis_result=analysis_data
            )
            
            answer = self._llm_to_str(self.llm.invoke(prompt))
            state["final_answer"] = self._critique_answer(
                question,
                answer,
                sql_result,
                search_result
            )
            state["final_answer"] = self._append_period_notice(
                state["final_answer"],
                intent,
                sql_result
            )
            state["messages"] = list(state["messages"]) + [AIMessage(content=state["final_answer"])]
            
        except Exception as e:
            state["final_answer"] = f"生成回答时出错：{str(e)}"
        
        return state
    
    def query(self, question: str, thread_id: str = "default", user_id: Optional[str] = None) -> str:
        """执行查询
        
        Args:
            question: 用户问题
            thread_id: 线程ID，用于区分不同的会话
            user_id: 用户ID，用于长期记忆
            
        Returns:
            回答结果
        """
        guard = self.guardrail_agent.check(question)
        if guard.get("decision") == "block":
            return "该请求存在潜在风险（可能包含注入或越权指令），已被 GuardrailAgent 拦截。请换一种更安全、明确的提问方式。"

        plan = self.planner_agent.plan(question)
        request_id = str(uuid.uuid4())
        req_t0 = time.time()

        initial_state = {
            "messages": [HumanMessage(content=question)],
            "user_question": question,
            "intent": None,
            "sql_result": None,
            "analysis_result": None,
            "search_result": None,
            "final_answer": None,
            "error": None,
            "metadata": {
                "thread_id": thread_id,
                "user_id": user_id,
                "request_id": request_id,
                "plan": plan,
                "trace": [
                    {"ts": int(time.time() * 1000), "step": "guardrail", "detail": guard.get("reason", "allow")},
                    {"ts": int(time.time() * 1000), "step": "plan", "detail": json.dumps(plan, ensure_ascii=False)}
                ]
            }
        }
        
        # 使用checkpointer保存会话状态
        config = {"configurable": {"thread_id": thread_id}}
        
        final_state = self.graph.invoke(initial_state, config)
        final_state.setdefault("metadata", {}).setdefault("metrics", {})["total_latency_ms"] = int((time.time() - req_t0) * 1000)
        
        answer = final_state.get("final_answer", "抱歉，无法处理你的问题。")
        
        # 获取完整的对话历史（已经包含了当前的问题和回答）
        all_messages = list(final_state["messages"])
        
        print(f"[记忆] 当前会话共有 {len(all_messages)} 条消息")
        
        # 自动提取并保存长期记忆
        if user_id:
            self._extract_and_save_memory(all_messages, user_id)
        
        return answer
    
    def _extract_and_save_memory(self, messages: Sequence[BaseMessage], user_id: str):
        """自动提取并保存长期记忆"""
        try:
            if not self.memory_extractor.should_extract(messages, threshold=6):
                return
            
            preferences = self.memory_extractor.extract_preferences_from_conversation(
                messages, user_id
            )
            for key, value in preferences.items():
                self.long_term_memory.save_preference(user_id, key, str(value))
            
            knowledge_list = self.memory_extractor.extract_knowledge_from_conversation(
                messages, user_id
            )
            for knowledge in knowledge_list:
                self.long_term_memory.save_knowledge(
                    user_id,
                    knowledge.get("category", "其他"),
                    knowledge.get("content", ""),
                    knowledge.get("confidence", 0.8)
                )
        except Exception as e:
            print(f"提取记忆失败: {e}")
    
    def stream_query(
        self,
        question: str,
        thread_id: str = "default",
        user_id: Optional[str] = None
    ) -> Generator[str, None, None]:
        """流式查询，以 SSE 格式生成事件流
        
        使用 LangGraph 的 graph.stream() 在每个节点完成后推送状态更新，
        最终 LLM 汇总回答以流式方式逐字输出。
        
        Yields:
            SSE 格式字符串：data: {...}\\n\\n
        """
        def sse(type_: str, **kwargs) -> str:
            return f"data: {json.dumps({'type': type_, **kwargs}, ensure_ascii=False)}\n\n"

        request_id = str(uuid.uuid4())
        req_t0 = time.time()
        yield sse("trace", step="request_id", detail=request_id)
        
        # --- 意图识别（直接调用，以便立即推送状态）---
        yield sse("status", message="正在执行 Guardrail 检查...")

        guard = self.guardrail_agent.check(question)
        yield sse("trace", step="guardrail", detail=guard.get("reason", ""))
        if guard.get("decision") == "block":
            blocked = "该请求存在潜在风险（可能包含注入或越权指令），已被 GuardrailAgent 拦截。请换一种更安全、明确的提问方式。"
            yield sse("chunk", content=blocked)
            yield sse("done", answer=blocked)
            return

        yield sse("status", message="Planner Agent 正在拆解任务...")
        plan = self.planner_agent.plan(question)
        yield sse("plan", plan=plan)
        yield sse("trace", step="plan", detail=plan.get("goal", ""))

        yield sse("status", message="正在识别问题意图...")
        
        user_context = ""
        if user_id:
            try:
                memory_ctx = self.memory_agent.build_user_context(user_id, question, top_k=3)
                user_context = memory_ctx.get("context_text", "")
            except Exception:
                pass
        
        # 从 checkpointer 获取对话历史
        config = {"configurable": {"thread_id": thread_id}}
        try:
            snapshot = self.graph.get_state(config)
            existing_msgs = list(snapshot.values.get("messages", []))
        except Exception:
            existing_msgs = []
        
        temp_state: MasterAgentState = {
            "messages": existing_msgs,
            "user_question": question,
            "intent": None,
            "sql_result": None,
            "analysis_result": None,
            "search_result": None,
            "final_answer": None,
            "error": None,
            "metadata": {"thread_id": thread_id, "user_id": user_id}
        }
        conversation_history = self._get_conversation_history(temp_state)
        
        intent_prompt = get_master_intent_prompt(question, conversation_history, user_context)
        try:
            raw = self.llm.invoke(intent_prompt)
            intent_raw = self._llm_to_str(raw).strip().lower()
            print(f"[意图识别] LLM 原始返回: {repr(intent_raw)}")
            valid_intents = [
                "simple_answer", "sql_only", "analysis_only",
                "sql_and_analysis", "web_search", "search_and_sql"
            ]
            intent = "sql_only"
            for vi in valid_intents:
                if vi in intent_raw:
                    intent = vi
                    break
        except Exception as e:
            intent = "simple_answer"
            err_msg = f"{type(e).__name__}: {e}"
            print(f"[意图识别] LLM 调用失败: {err_msg}")
            # 尝试获取更详细的错误信息（如 API 配额不足等）
            if "FreeTierOnly" in str(e) or "Quota" in str(e):
                err_msg = "通义千问 API 免费额度已用完，请在控制台开通付费或更换模型。"
            elif "InvalidApiKey" in str(e) or "Unauthorized" in str(e):
                err_msg = "DASHSCOPE_API_KEY 无效，请检查 API Key 是否正确。"
            yield sse("error", message=f"LLM 调用失败: {err_msg}")
        
        yield sse("intent", intent=intent)

        router_mode = "direct"
        if router_mode == "enhanced":
            route = self.tool_router_agent.route(
                question=question,
                intent=intent,
                available_tools=["sql", "analysis", "search", "memory", "summary"]
            )
            intent = route.get("path", intent)
            yield sse("trace", step="tool_route", detail=f"{intent} ({route.get('reason', 'n/a')})")
        else:
            yield sse("trace", step="tool_route", detail=f"{intent} (intent_direct)")
        
        # --- 搜索不可用时降级 ---
        if intent in ("web_search", "search_and_sql") and not self.search_agent.available:
            msg = "联网搜索功能暂未启用，请配置 TAVILY_API_KEY 环境变量后重启。申请地址：https://tavily.com"
            yield sse("chunk", content=msg)
            yield sse("done", answer=msg)
            return
        
        sql_result = None
        analysis_result = None
        search_result = None
        final_answer = ""
        
        # --- 执行各子任务 ---
        if intent == "simple_answer":
            yield sse("status", message="正在生成回答...")
            final_answer = (
                "你好！我是航班-天气分析助手，可以帮你查询航班与天气数据、"
                "进行延误与运营分析，还支持联网搜索做行业对比。有什么可以帮你的吗？"
            )
            yield sse("chunk", content=final_answer)
        
        else:
            # SQL 查询（适用于 sql_only / sql_and_analysis / search_and_sql）
            if intent in ("sql_only", "sql_and_analysis", "search_and_sql"):
                yield sse("status", message="正在查询数据库...")
                t_sql = time.time()
                sql_result = self.sql_agent.query(question)
                
                if sql_result.get("sql"):
                    yield sse(
                        "sql",
                        sql=sql_result["sql"],
                        retry_count=sql_result.get("retry_count", 0),
                        latency_ms=sql_result.get("latency_ms")
                    )
                yield sse(
                    "trace",
                    step="call_sql",
                    detail=f"latency_ms={int((time.time() - t_sql) * 1000)}, failure_stage={sql_result.get('failure_stage')}"
                )
                if sql_result.get("error"):
                    yield sse("error", message=f"数据库查询出错: {sql_result['error']}")
                
                # 保存会话数据
                if thread_id not in self.session_data:
                    self.session_data[thread_id] = {}
                self.session_data[thread_id]["last_sql_result"] = sql_result
            
            # 数据分析（适用于 analysis_only / sql_and_analysis）
            if intent in ("analysis_only", "sql_and_analysis"):
                yield sse("status", message="正在分析数据...")
                
                data_to_analyze = None
                if sql_result and sql_result.get("data"):
                    data_to_analyze = sql_result["data"]
                elif thread_id in self.session_data:
                    last = self.session_data[thread_id].get("last_sql_result", {})
                    data_to_analyze = last.get("data") if last else None
                
                if data_to_analyze:
                    analysis_result = self.analysis_agent.analyze(data_to_analyze, question)
                    if analysis_result.get("chart"):
                        yield sse("chart", config=analysis_result["chart"])
                else:
                    yield sse("error", message="没有可分析的数据，请先执行数据查询")
            
            # 纯联网搜索
            if intent == "web_search":
                yield sse("status", message="正在联网搜索...")
                search_result = self.search_agent.search(question)
                if search_result.get("sources"):
                    yield sse("sources", sources=search_result["sources"])
                if search_result.get("error"):
                    yield sse("error", message=search_result["error"])
            
            # 搜索 + 数据库联合分析
            if intent == "search_and_sql":
                yield sse("status", message="正在并行执行 SQL 与联网搜索...")

                if not sql_result:
                    sql_result = self.sql_agent.query(question)

                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    fut_sql = pool.submit(lambda: sql_result)
                    fut_search = pool.submit(self.search_agent.search, question)
                    sql_result = fut_sql.result()
                    base_search_result = fut_search.result()

                quality = base_search_result.get("quality", {}) if isinstance(base_search_result, dict) else {}
                debate_result = self.debate_agent.adjudicate(
                    question,
                    str(sql_result.get("data") or sql_result.get("error") or "无SQL证据"),
                    str(base_search_result.get("answer") or base_search_result.get("error") or "无搜索证据")
                )

                search_result = {
                    "answer": debate_result.get("answer", ""),
                    "sources": base_search_result.get("sources", []),
                    "error": base_search_result.get("error") if base_search_result.get("error") and sql_result.get("error") else None,
                    "quality": quality,
                    "debate": {
                        "status": debate_result.get("status", "unknown"),
                        "scorecard": debate_result.get("scorecard", {})
                    }
                }
                if debate_result.get("status") == "low_evidence":
                    yield sse("trace", step="debate", detail="low_evidence_threshold_triggered")
                else:
                    yield sse("trace", step="debate", detail="search_and_sql_adjudicated")

                yield sse(
                    "quality",
                    quality={
                        "search_quality": quality,
                        "debate": search_result.get("debate", {}),
                        "confidence_label": self._compute_confidence_label(search_result)
                    }
                )
                if search_result.get("sources"):
                    yield sse("sources", sources=search_result["sources"])
                if search_result.get("error"):
                    yield sse("error", message=search_result["error"])
            
            # --- 生成最终回答（流式输出 LLM 结果）---
            yield sse("status", message="正在生成回答...")
            
            if intent in ("web_search", "search_and_sql") and search_result:
                # 搜索智能体已生成完整回答，直接流式输出
                answer_text = search_result.get("answer", "未能获取搜索结果")
                if search_result.get("error"):
                    answer_text = f"搜索出错：{search_result['error']}"
                else:
                    sources = search_result.get("sources", [])
                    if sources:
                        answer_text += "\n\n**参考来源：**\n" + "\n".join(
                            f"- {url}" for url in sources[:5]
                        )
                confidence_label = self._compute_confidence_label(search_result)
                answer_text = self._prepend_confidence_banner(answer_text, confidence_label)
                final_answer = self._critique_answer(question, answer_text, sql_result, search_result)
                yield sse("chunk", content=final_answer)
            
            elif sql_result and sql_result.get("error"):
                final_answer = f"数据库查询出错：{sql_result['error']}"
                yield sse("chunk", content=final_answer)
            
            else:
                # 使用 LLM 流式生成汇总回答
                sql_data = sql_result.get("data") if sql_result else None
                analysis_data = analysis_result.get("analysis") if analysis_result else None
                
                summary_prompt = get_summary_prompt(
                    question=question,
                    sql_result=sql_data,
                    analysis_result=analysis_data
                )
                
                try:
                    import re
                    in_think = False
                    think_buffer = ""
                    for chunk in self.llm.stream(summary_prompt):
                        if isinstance(chunk, str):
                            chunk_text = chunk
                        elif hasattr(chunk, 'content'):
                            chunk_text = chunk.content
                        elif hasattr(chunk, 'text'):
                            chunk_text = chunk.text
                        else:
                            chunk_text = str(chunk)
                        
                        # 过滤 <think>...</think> 思考内容，不发送给前端
                        think_buffer += chunk_text
                        if '<think>' in think_buffer and not in_think:
                            in_think = True
                        if in_think:
                            if '</think>' in think_buffer:
                                cleaned = re.sub(r'<think>[\s\S]*?</think>', '', think_buffer).strip()
                                if cleaned:
                                    final_answer += cleaned
                                    yield sse("chunk", content=cleaned)
                                think_buffer = ""
                                in_think = False
                            continue
                        
                        think_buffer = ""
                        final_answer += chunk_text
                        yield sse("chunk", content=chunk_text)
                except Exception as e:
                    raw = self.llm.invoke(summary_prompt)
                    final_answer = self._llm_to_str(raw)
                    final_answer = self._critique_answer(question, final_answer, sql_result, search_result)
                    yield sse("chunk", content=final_answer)
        
        yield sse("done", answer=final_answer)
        yield sse("trace", step="request_done", detail=f"latency_ms={int((time.time() - req_t0) * 1000)}")
        
        # --- 保存对话历史到 LangGraph checkpointer ---
        try:
            new_messages = [HumanMessage(content=question), AIMessage(content=final_answer)]
            self.graph.update_state(
                config,
                {"messages": new_messages},
                as_node="summarize"
            )
            all_msgs = existing_msgs + new_messages
            if user_id:
                self._extract_and_save_memory(all_msgs, user_id)
        except Exception as e:
            print(f"保存对话历史失败（不影响本次回答）: {e}")

