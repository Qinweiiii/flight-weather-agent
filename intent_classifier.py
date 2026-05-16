"""
意图分类器

两层策略：
1. 规则层：正则匹配高频明确意图，置信度直接返回 1.0
2. Embedding 层：用 DashScope text-embedding-v3 计算 cosine 相似度
   供 MasterAgent 的 ensemble 计算使用

用法示例：
    clf = IntentClassifier(api_key="sk-...", base_url="https://...")
    intent, conf = clf.classify(question)            # 规则层
    intent, conf = clf.embedding_similarity(question) # Embedding 层
"""

import re
from typing import Optional, Tuple

import numpy as np
from langchain_openai import OpenAIEmbeddings

from intent import Intent


# 每种意图的原型句子（用于 embedding cosine similarity）
_PROTOTYPES: dict[Intent, list[str]] = {
    Intent.SIMPLE_ANSWER: [
        "你好", "帮助", "谢谢", "再见", "你能做什么", "你是谁",
    ],
    Intent.SQL_ONLY: [
        "最近30天出发延误超过60分钟的航班数量",
        "统计各航司平均到达延误时间",
        "前5名延误率最高的出发机场",
        "2025年第一季度被取消的航班比例是多少",
        "哪些航线航班总数超过500次",
        "近90天降雨量大于5mm的航班有多少",
    ],
    Intent.ANALYSIS_ONLY: [
        "帮我分析一下上面的查询结果",
        "根据这些数据给出运营改进建议",
        "从刚才的数据来看有什么规律",
    ],
    Intent.SQL_AND_ANALYSIS: [
        "查询最近90天各航司延误数据并分析原因",
        "统计取消率并给出改进建议",
        "列出延误前10机场并分析影响因素",
    ],
    Intent.WEB_SEARCH: [
        "最新的航空公司延误政策是什么",
        "今天有什么航空业新闻",
        "搜索一下国际民航组织最新规定",
        "目前行业内平均延误率是多少",
    ],
    Intent.SEARCH_AND_SQL: [
        "结合行业标准对比我们内部的延误率表现",
        "对比外部公开数据和数据库分析我们的竞争力",
        "搜索行业基准并和内部数据比较",
    ],
}

# 默认置信度阈值，低于此值时上层应向用户确认
CONFIDENCE_THRESHOLD = 0.75


class IntentClassifier:
    """意图分类器（规则优先，Embedding 相似度兜底）"""

    def __init__(self, api_key: str, base_url: str):
        self._embed_model = OpenAIEmbeddings(
            model="text-embedding-v3",
            api_key=api_key,
            base_url=base_url,
        )
        # Lazy: 首次调用 embedding_similarity 时计算原型向量
        self._proto_vecs: Optional[dict[Intent, np.ndarray]] = None

    # ------------------------------------------------------------------
    # 规则层（公共）
    # ------------------------------------------------------------------

    def classify(self, question: str) -> Optional[Tuple[Intent, float]]:
        """
        规则匹配。命中则返回 (Intent, 1.0)，未命中返回 None。
        未命中时应由上层继续调用 embedding_similarity 和 LLM。
        """
        return self._rule_intent(question)

    # ------------------------------------------------------------------
    # Embedding 层（公共）
    # ------------------------------------------------------------------

    def embedding_similarity(self, question: str) -> Tuple[Intent, float]:
        """
        计算 question 与各意图原型向量的 cosine 相似度，
        返回最相似的意图及归一化置信度（0~1）。
        """
        self._ensure_proto_vecs()

        q_vec = np.array(self._embed_model.embed_query(question))
        q_norm = q_vec / (np.linalg.norm(q_vec) + 1e-9)

        best_intent = Intent.SQL_ONLY
        best_score = -1.0

        for intent, proto_vec in self._proto_vecs.items():
            score = float(np.dot(q_norm, proto_vec))
            if score > best_score:
                best_score = score
                best_intent = intent

        # cosine 相似度范围 [-1, 1]，映射到 [0, 1]
        confidence = (best_score + 1.0) / 2.0
        return best_intent, confidence

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    @staticmethod
    def _rule_intent(question: str) -> Optional[Tuple[Intent, float]]:
        q = question

        # SQL - 数量统计
        if re.search(r"多少|数量|总数|总量|共有|有几|几个|几班|几次", q):
            return Intent.SQL_ONLY, 1.0
        # SQL - 排名 / Top‑K
        if re.search(r"前\s*\d+\s*[名个]|top\s*\d+|排名|排行|最高|最低|最多|最少", q, re.I):
            return Intent.SQL_ONLY, 1.0
        # SQL - 比例
        if re.search(r"比例|占比|百分比|percent|ratio", q, re.I):
            return Intent.SQL_ONLY, 1.0
        # SQL - 平均
        if re.search(r"平均|avg|average", q, re.I):
            return Intent.SQL_ONLY, 1.0
        # 联网搜索
        if re.search(r"最新|新闻|实时|搜索.*网上|网上.*查", q):
            return Intent.WEB_SEARCH, 1.0
        # 简单问答
        if re.search(r"^(你好|hi|hello|帮助|help|再见|bye|谢谢|感谢)$", q.strip(), re.I):
            return Intent.SIMPLE_ANSWER, 1.0

        return None

    def _ensure_proto_vecs(self):
        """Lazy 计算每个意图的原型向量（各原型句向量的均值，已 L2 归一化）。"""
        if self._proto_vecs is not None:
            return

        self._proto_vecs = {}
        for intent, sentences in _PROTOTYPES.items():
            vecs = np.array(self._embed_model.embed_documents(sentences))
            mean_vec = vecs.mean(axis=0)
            self._proto_vecs[intent] = mean_vec / (np.linalg.norm(mean_vec) + 1e-9)
