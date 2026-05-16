"""
数据分析子智能体

负责对查询结果进行深度分析，生成洞察和建议，并自动生成 ECharts 图表配置。
"""

import json
from typing import Dict, Any, Optional, List
from pathlib import Path

from langchain_core.language_models import BaseLLM

import sys
sys.path.append(str(Path(__file__).parent.parent))
from prompts import get_analysis_prompt, get_chart_config_prompt


class DataAnalysisAgent:
    """数据分析子智能体，支持文字分析和 ECharts 图表可视化"""
    
    def __init__(self, llm: BaseLLM):
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
    
    def _parse_data(self, data_str: str) -> Optional[Any]:
        """解析数据字符串"""
        try:
            return json.loads(data_str)
        except Exception:
            return None
    
    def _prepare_data_summary(self, data: Any) -> str:
        """准备数据摘要"""
        if isinstance(data, list):
            if len(data) == 0:
                return "数据为空"
            
            summary = f"数据总数: {len(data)}条记录\n"
            summary += "数据示例:\n"
            for i, item in enumerate(data[:3]):
                summary += f"  记录{i+1}: {json.dumps(item, ensure_ascii=False)}\n"
            
            if len(data) > 0 and isinstance(data[0], dict):
                numeric_fields = [k for k, v in data[0].items() if isinstance(v, (int, float))]
                
                if numeric_fields:
                    summary += "\n数值字段统计:\n"
                    for field in numeric_fields:
                        values = [item[field] for item in data if field in item and isinstance(item[field], (int, float))]
                        if values:
                            summary += f"  {field}: 最小={min(values)}, 最大={max(values)}, 平均={sum(values)/len(values):.2f}\n"
            
            return summary
        
        elif isinstance(data, dict):
            return f"单条记录: {json.dumps(data, ensure_ascii=False)}"
        
        return str(data)
    
    def _should_generate_chart(self, data: Any) -> bool:
        """判断是否适合生成图表
        
        图表生成条件：
        - 列表数据
        - 至少2条记录
        - 包含数值字段
        """
        if not isinstance(data, list) or len(data) < 2:
            return False
        if not isinstance(data[0], dict):
            return False
        has_numeric = any(isinstance(v, (int, float)) for v in data[0].values())
        return has_numeric
    
    def _generate_chart_config(self, data: Any, data_summary: str, context: str = "") -> Optional[Dict]:
        """生成 ECharts 图表配置
        
        让 LLM 根据数据特征自动选择图表类型（柱状图/折线图/饼图）
        并生成完整的 ECharts option 配置对象。
        
        Args:
            data: 解析后的数据
            data_summary: 数据摘要
            context: 上下文信息
            
        Returns:
            ECharts option 配置字典，或 None（生成失败时）
        """
        try:
            raw_data_str = json.dumps(data[:20], ensure_ascii=False)  # 最多传入20条数据
            prompt = get_chart_config_prompt(
                data_summary=data_summary,
                raw_data=raw_data_str,
                context=context
            )
            
            chart_json_str = self._llm_to_str(self.llm.invoke(prompt)).strip()
            
            # 清理可能的代码块标记
            if chart_json_str.startswith("```json"):
                chart_json_str = chart_json_str[7:]
            elif chart_json_str.startswith("```"):
                chart_json_str = chart_json_str[3:]
            if chart_json_str.endswith("```"):
                chart_json_str = chart_json_str[:-3]
            chart_json_str = chart_json_str.strip()
            
            chart_config = json.loads(chart_json_str)
            
            # 基本校验：必须是dict且包含series
            if isinstance(chart_config, dict) and "series" in chart_config:
                return chart_config
            return None
            
        except Exception as e:
            print(f"[图表生成] 图表配置生成失败（不影响分析结果）: {e}")
            return None
    
    def analyze(self, data: str, context: str = "") -> Dict[str, Any]:
        """分析数据，同时生成文字分析和 ECharts 图表配置
        
        Args:
            data: JSON格式的数据字符串
            context: 上下文信息（如原始问题）
            
        Returns:
            {
                "analysis": 文字分析内容,
                "chart": ECharts option配置字典（无法生成时为None）,
                "error": 错误信息（成功时为None）
            }
        """
        result = {
            "analysis": None,
            "chart": None,
            "error": None
        }
        
        try:
            parsed_data = self._parse_data(data)
            
            if parsed_data is None:
                result["error"] = "无法解析数据"
                return result
            
            if isinstance(parsed_data, dict) and "error" in parsed_data:
                result["error"] = f"数据包含错误: {parsed_data['error']}"
                return result
            
            data_summary = self._prepare_data_summary(parsed_data)
            
            # 文字分析
            prompt = get_analysis_prompt(
                data_summary=data_summary,
                raw_data=data,
                context=context
            )
            analysis = self._llm_to_str(self.llm.invoke(prompt))
            result["analysis"] = analysis
            
            # ECharts 图表配置（仅对适合可视化的数据生成）
            if self._should_generate_chart(parsed_data):
                chart_config = self._generate_chart_config(parsed_data, data_summary, context)
                result["chart"] = chart_config
            
        except Exception as e:
            result["error"] = f"分析失败: {str(e)}"
        
        return result

