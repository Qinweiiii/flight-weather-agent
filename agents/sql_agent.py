"""
SQL查询子智能体

负责将自然语言转换为SQL并执行查询，支持自动纠错循环（最多3次重试）。
Reflection 模式：执行失败时将错误信息反馈给 LLM 重新生成。
"""

import json
import sqlite3
import sys
import asyncio
import concurrent.futures
import time
import re
import os
from typing import Dict, Any
from pathlib import Path

from langchain_core.language_models import BaseLLM
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.append(str(Path(__file__).parent.parent))
from prompts import get_few_shot_prompt, get_sql_correction_prompt


class SQLQueryAgent:
    """SQL查询子智能体，支持自动纠错循环（ReAct/Reflection 模式）"""
    
    def __init__(self, llm: BaseLLM, db_path: str, num_examples: int = 3):
        """初始化SQL查询智能体
        
        Args:
            llm: 语言模型实例
            db_path: 数据库路径
            num_examples: Few-shot示例数量
        """
        self.llm = llm
        self.db_path = db_path
        self.num_examples = num_examples
        self._time_anchor_cache = None
    
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
    
    def _get_schema(self) -> str:
        """获取数据库Schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)
        tables = cursor.fetchall()
        
        schema_text = ""
        for table in tables:
            table_name = table[0]
            schema_text += f"\n表：{table_name}\n"
            
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = cursor.fetchall()
            
            for col in columns:
                cid, name, dtype, notnull, default, pk = col
                pk_text = " (主键)" if pk else ""
                notnull_text = " NOT NULL" if notnull else ""
                schema_text += f"  - {name}: {dtype}{notnull_text}{pk_text}\n"
        
        conn.close()
        return schema_text.strip()

    def _get_data_time_anchor(self) -> Dict[str, Any]:
        """获取业务数据时间锚点（全库最大时间），用于替代 NOW/CURRENT_DATE。"""
        if self._time_anchor_cache is not None:
            return self._time_anchor_cache

        anchor = {
            "min_ts": None,
            "max_ts": None,
            "source": None
        }

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

                    if anchor["max_ts"] is None or str(max_ts) > str(anchor["max_ts"]):
                        anchor["max_ts"] = str(max_ts)
                        anchor["min_ts"] = str(min_ts) if min_ts else None
                        anchor["source"] = f"{table}.{col}"
                except Exception:
                    continue
        finally:
            conn.close()

        self._time_anchor_cache = anchor
        return anchor

    def _apply_time_anchor(self, sql: str) -> str:
        """Preserve SQL literals; the time contract is supplied before generation."""
        return sql
    
    def _clean_sql(self, sql: str) -> str:
        """清理SQL语句（移除代码块标记和多余前缀）"""
        sql = sql.strip()
        if sql.startswith("```sql"):
            sql = sql[6:]
        elif sql.startswith("```"):
            sql = sql[3:]
        
        prefixes = ["SQL：", "SQL:", "sql:", "sql："]
        for prefix in prefixes:
            if sql.startswith(prefix):
                sql = sql[len(prefix):]
                break
        
        if sql.endswith("```"):
            sql = sql[:-3]
        
        return sql.strip()

    def _is_detail_request(self, question: str) -> bool:
        """判断用户是否明确要求展示明细/具体数据。"""
        q = (question or "").lower()
        detail_keywords = [
            "具体数据", "明细", "展示一下", "展示", "列出来", "逐条", "详情",
            "具体航班", "样本", "明细数据", "show details", "detail"
        ]
        return any(k in q for k in detail_keywords)

    def _looks_aggregate_only(self, sql: str) -> bool:
        """粗略判断 SQL 是否仅聚合不返回明细。"""
        s = (sql or "").lower()
        has_agg = any(tok in s for tok in ["count(", "sum(", "avg(", "min(", "max("])
        has_detail_col = any(tok in s for tok in ["fl_date", "origin", "dest", "op_unique_carrier", "arr_delay", "dep_delay"])
        return has_agg and not has_detail_col

    def _expand_sql_for_detail_request(self, question: str, aggregate_sql: str) -> str:
        """当用户要求“具体数据”时，将聚合 SQL 扩展为“明细 + 汇总窗口列”形式。"""
        schema = self._get_schema()
        prompt = f"""你是SQL专家。用户要求查看“具体数据/明细”，但当前SQL偏聚合。

数据库Schema：
{schema}

用户问题：{question}

当前SQL：
{aggregate_sql}

请改写为“可直接展示明细行”的SQL，并同时保留总量信息，要求：
1. 返回逐行明细（如 FL_DATE、OP_UNIQUE_CARRIER、ORIGIN、DEST、ARR_DELAY、DEP_DELAY、CANCELLED 等核心列）
2. 使用窗口函数携带汇总值，例如 COUNT(*) OVER() AS total_flights_30d
3. 如果问题有“最近/近30天”，必须使用数据表 MAX(时间列) 作为时间锚点，不得使用 now()/current_date/current_timestamp
4. 最终 SQL 只能是一条只读 SELECT/CTE 语句
5. 为便于展示，加 ORDER BY 时间倒序，且 LIMIT 30

只返回SQL语句，不要解释。"""

        new_sql = self._llm_to_str(self.llm.invoke(prompt)).strip()
        new_sql = self._clean_sql(new_sql)
        new_sql = self._apply_time_anchor(new_sql)
        return new_sql
    
    def _generate_sql(self, question: str) -> str:
        """生成SQL语句"""
        schema = self._get_schema()
        prompt = get_few_shot_prompt(
            question=question,
            schema=schema,
            num_examples=self.num_examples
        )
        from tools.metrics import METRICS
        prompt += f"\n实际数据时间范围：{self._get_data_time_anchor()}。"
        prompt += f"\n强制指标约定（优先于旧示例）：到达延误率表达式为 {METRICS['arr_delay_rate']}；平均到达延误为 {METRICS['avg_arr_delay']}。NULL 不等于 0。最近 N 天包含最大日期，应减 N-1 天。正延误总分钟用 SUM(MAX(ARR_DELAY,0))，不要用提前到达抵消延误。天气分组未知值单列；降水与取消同时出现不证明天气导致取消。禁止将本地样本称为完整行业总体。"
        sql = self._llm_to_str(self.llm.invoke(prompt)).strip()
        sql = self._clean_sql(sql)
        sql = self._apply_time_anchor(sql)
        return sql

    def _validate_readonly_sql(self, sql: str) -> Dict[str, Any]:
        from tools.sql_executor import validate_readonly_sql
        return validate_readonly_sql(sql)
    
    def _correct_sql(self, question: str, original_sql: str, error_msg: str, attempt: int) -> str:
        """SQL 自动纠错（Reflection 模式）
        
        将错误信息和原始 SQL 反馈给 LLM，要求其重新生成正确的 SQL。
        
        Args:
            question: 用户原始问题
            original_sql: 出错的 SQL 语句
            error_msg: 错误信息
            attempt: 当前重试次数（从1开始）
            
        Returns:
            修正后的 SQL 语句
        """
        schema = self._get_schema()
        prompt = get_sql_correction_prompt(
            question=question,
            schema=schema,
            original_sql=original_sql,
            error_msg=error_msg,
            attempt=attempt
        )
        from prompts import SYSTEM_PROMPT
        prompt += '\n修复仍须遵守原业务口径：\n' + SYSTEM_PROMPT.format(schema=schema)
        corrected = self._llm_to_str(self.llm.invoke(prompt)).strip()
        corrected = self._clean_sql(corrected)
        corrected = self._apply_time_anchor(corrected)
        return corrected
    
    async def _execute_sql_via_mcp(self, sql: str) -> str:
        """通过MCP工具执行SQL
        
        Args:
            sql: SQL语句
            
        Returns:
            查询结果JSON字符串
        """
        mcp_script = Path(__file__).parent.parent / "mcp_sql_server.py"
        server_params = StdioServerParameters(
            command=sys.executable,
            args=[str(mcp_script)],
            env={**os.environ, "FLIGHT_DB_PATH": str(Path(self.db_path).resolve())}
        )
        
        async with stdio_client(server_params) as (read, write):
            from datetime import timedelta
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as session:
                await session.initialize()
                
                result = await session.call_tool(
                    "execute_sql", 
                    arguments={"sql": sql}
                )
                
                if result.content:
                    return result.content[0].text
                return json.dumps({"error": "无返回结果"})
    
    def _run_async(self, coro):
        """安全地执行异步代码，兼容已有事件循环（如 httpx/openai 遗留的）"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        
        if loop and loop.is_running():
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        else:
            return asyncio.run(coro)
    
    def query(self, question: str, max_retries: int = 3, initial_sql: str = None) -> Dict[str, Any]:
        """执行查询，失败时自动纠错并重试（Reflection 循环）
        
        流程：SQL生成 → 执行 → [失败] → 错误反馈给LLM → 重新生成 → 最多重试 max_retries 次
        
        Args:
            question: 用户问题
            max_retries: 最大重试次数（默认3次）
            
        Returns:
            {
                "sql": 最终执行的SQL,
                "data": 查询结果JSON字符串（成功时）,
                "error": 错误信息（成功时为None）,
                "retry_count": 实际重试次数（0表示首次成功）
            }
        """
        result = {
            "sql": None,
            "data": None,
            "error": None,
            "retry_count": 0,
            "failure_stage": None,
            "latency_ms": None,
            "attempts": []
        }

        start_ts = time.time()
        stage = 'generate'
        try:
            if not isinstance(max_retries, int) or not 0 <= max_retries <= 5:
                raise ValueError('max_retries must be 0..5')
            sql = initial_sql if initial_sql is not None else self._generate_sql(question)

            if self._is_detail_request(question) and self._looks_aggregate_only(sql):
                sql = self._expand_sql_for_detail_request(question, sql)

            result["sql"] = sql
            
            if not sql:
                result["error"] = "未能生成有效的SQL"
                result["failure_stage"] = "generate"
                return result

            sql_check = self._validate_readonly_sql(sql)
            if not sql_check["ok"]:
                result["error"] = f"SQL 安全校验未通过: {sql_check['reason']}"
                result["failure_stage"] = "safety_check"
                return result
            
            for attempt in range(max_retries + 1):
                stage = 'mcp_transport'
                attempt_started = time.monotonic()
                query_result = self._run_async(self._execute_sql_via_mcp(sql))
                result_data = json.loads(query_result)
                result["attempts"].append({"sql": sql, "error": result_data.get("error") if isinstance(result_data, dict) else None,
                                           "latency_ms": int((time.monotonic()-attempt_started)*1000)})
                
                if isinstance(result_data, dict) and "error" in result_data:
                    error_msg = result_data["error"]
                    
                    if attempt < max_retries:
                        stage = 'reflection'
                        print(f"[SQL纠错] 第{attempt + 1}次执行失败: {error_msg}，正在让LLM自动修复...")
                        sql = self._correct_sql(question, sql, error_msg, attempt + 1)

                        if self._is_detail_request(question) and self._looks_aggregate_only(sql):
                            sql = self._expand_sql_for_detail_request(question, sql)

                        result["sql"] = sql
                        result["retry_count"] = attempt + 1

                        sql_check = self._validate_readonly_sql(sql)
                        if not sql_check["ok"]:
                            result["error"] = f"SQL 自动修复后未通过安全校验: {sql_check['reason']}"
                            result["failure_stage"] = "safety_check_after_correction"
                            break
                    else:
                        result["error"] = f"SQL执行失败（已自动重试{attempt}次）: {error_msg}"
                        result["failure_stage"] = "execute"
                else:
                    if not isinstance(result_data, list):
                        raise ValueError('invalid_mcp_result: expected rows or error')
                    result["data"] = query_result
                    if attempt > 0:
                        print(f"[SQL纠错] 第{attempt}次修复后执行成功")
                    break
                    
        except Exception as e:
            result["error"] = f"查询失败: {str(e)}"
            if not result.get("failure_stage"):
                result["failure_stage"] = stage
        finally:
            result["latency_ms"] = int((time.time() - start_ts) * 1000)
        
        return result
