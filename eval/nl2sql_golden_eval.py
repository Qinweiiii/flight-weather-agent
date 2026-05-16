import os
import sys
import json
import sqlite3
import yaml
from pathlib import Path
from typing import List, Dict, Any, Tuple

# 添加根路径以便引入模块
sys.path.append(str(Path(__file__).parent.parent))
from agents.sql_agent import SQLQueryAgent
from langchain_openai import ChatOpenAI

def load_config() -> Dict[str, Any]:
    config_path = Path(__file__).parent.parent / "config" / "config.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 解析环境变量
    for k, v in config.get("llm", {}).items():
        if isinstance(v, str) and v.startswith("${") and v.endswith("}"):
            config["llm"][k] = os.getenv(v[2:-1], v)
    return config

def execute_sql_locally(db_path: str, sql: str) -> List[Dict]:
    """本地直接执行 SQL 并转为字典列表，用于和Agent结果比对"""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row  # 返回字典格式
        cursor = conn.cursor()
        cursor.execute(sql)
        rows = [dict(row) for row in cursor.fetchall()]
        return rows
    except Exception as e:
        print(f"本地执行报错 ({sql}): {e}")
        return []
    finally:
        if 'conn' in locals():
            conn.close()

def normalize_results(data) -> set:
    """将数据转换为可以哈希和比较的无序集合（忽略列名顺序，只需值一致）"""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except:
            return set()
    
    if not isinstance(data, list):
        return set()

    normalized_set = set()
    for row in data:
        # 将行内的所有 values (比如数字、字符串) 提取出来，数值型统一保留 5 位小数后转为字符串
        def _norm(v):
            if isinstance(v, float):
                # 避免浮点误差导致集合不匹配
                return f"{v:.5f}"
            return str(v)
        sorted_values = tuple(sorted([_norm(v) for v in row.values() if v is not None]))
        normalized_set.add(sorted_values)
    
    return normalized_set

# -- 核心：黄金业务测试集 --
GOLDEN_DATASET = [
    {
        "id": "1",
        "question": "最近30天出发延误大于60分钟的航班数量是多少？",
        "expected_sql": "SELECT COUNT(*) FROM flights_enriched WHERE DEP_DELAY > 60 AND FL_DATE >= date((SELECT MAX(FL_DATE) FROM flights_enriched), '-30 days')"
    },
    {
        "id": "2",
        "question": "统计近90天内被取消的航班数量。",
        "expected_sql": "SELECT sum(CANCELLED) FROM flights_enriched WHERE FL_DATE >= date((SELECT MAX(FL_DATE) FROM flights_enriched), '-90 days')"
    },
    {
        "id": "3",
        "question": "列出总到达延误时长排名前5的到达机场(DEST)",
        "expected_sql": "SELECT DEST, sum(ARR_DELAY) AS total_arr_delay FROM flights_enriched GROUP BY DEST ORDER BY total_arr_delay DESC LIMIT 5"
    },
    {
        "id": "4",
        "question": "出发地降雨量（prcp_ORIGIN）超过5的航班有多少？",
        "expected_sql": "SELECT COUNT(*) FROM flights_enriched WHERE prcp_ORIGIN > 5"
    }, 
    {
        "id": "5",
        "question": "统计2025年1月1日出发的所有航班的平均出发延误时间。",
        "expected_sql": "SELECT AVG(DEP_DELAY) FROM flights_enriched WHERE FL_DATE = '2025-01-01'"
    },
    {
        "id": "6",
        "question": "哪个出发机场（ORIGIN）在2025年2月的航班取消率最高？",
        "expected_sql": "SELECT ORIGIN, AVG(CANCELLED) AS cancel_rate FROM flights_enriched WHERE FL_DATE BETWEEN '2025-02-01' AND '2025-02-29' GROUP BY ORIGIN ORDER BY cancel_rate DESC LIMIT 1"
    },
    {
        "id": "7",
        "question": "2025年3月降雨量超过1的天数有多少？",
        "expected_sql": "SELECT COUNT(DISTINCT FL_DATE) FROM flights_enriched WHERE (prcp_origin > 1 OR prcp_dest > 1) AND FL_DATE BETWEEN '2025-03-01' AND '2025-03-31'"
    },
    {
        "id": "8",
        "question": "2025年第一季度到达延误总时长最多的到达机场是哪个？",
        "expected_sql": "SELECT DEST, SUM(ARR_DELAY) AS total_arr_delay FROM flights_enriched WHERE FL_DATE BETWEEN '2025-01-01' AND '2025-03-31' GROUP BY DEST ORDER BY total_arr_delay DESC LIMIT 1"
    },
    {
        "id": "9",
        "question": "2025年所有航班中，出发延误超过30分钟的航班比例是多少？",
        "expected_sql": "SELECT CAST(SUM(CASE WHEN DEP_DELAY > 30 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) FROM flights_enriched WHERE FL_DATE BETWEEN '2025-01-01' AND '2025-12-31'"
    },
    {
        "id": "10",
        "question": "统计到达延误率（ARR_DEL15=1的占比）最高的3家航司。",
        "expected_sql": "SELECT OP_UNIQUE_CARRIER, AVG(COALESCE(ARR_DEL15, 0)) AS delay_rate FROM flights_enriched GROUP BY OP_UNIQUE_CARRIER ORDER BY delay_rate DESC LIMIT 3"
    },
    {
        "id": "11",
        "question": "列出所有因为天气原因（出发地或到达地降雨量大于0）而取消的航班数量。",
        "expected_sql": "SELECT COUNT(*) FROM flights_enriched WHERE CANCELLED = 1 AND (COALESCE(prcp_ORIGIN, 0) > 0 OR COALESCE(prcp_DEST, 0) > 0)"
    },
    {
        "id": "12",
        "question": "最近30天，哪家航司的平均出发延误时间最短？",
        "expected_sql": "SELECT OP_UNIQUE_CARRIER, AVG(COALESCE(DEP_DELAY, 0)) AS avg_dep_delay FROM flights_enriched WHERE FL_DATE >= date((SELECT MAX(FL_DATE) FROM flights_enriched), '-30 day') GROUP BY OP_UNIQUE_CARRIER ORDER BY avg_dep_delay ASC LIMIT 1"
    },
    {
        "id": "13",
        "question": "哪些航线（按ORIGIN和DEST分组）的航班总数超过了500次？",
        "expected_sql": "SELECT ORIGIN, DEST, COUNT(*) AS route_flights FROM flights_enriched GROUP BY ORIGIN, DEST HAVING route_flights > 500"
    },
    {
        "id": "14",
        "question": "找出平均到达延误超过30分钟，并且总航班量大于100的到达机场(DEST)。",
        "expected_sql": "SELECT DEST, AVG(ARR_DELAY) AS avg_delay, COUNT(*) AS total_flights FROM flights_enriched GROUP BY DEST HAVING avg_delay > 30 AND total_flights > 100"
    },
    {
        "id": "15",
        "question": "查一下2025年2月，总起飞航班数最多的机场，它的平均出发延误是多少？",
        "expected_sql": "SELECT ORIGIN, AVG(DEP_DELAY) AS avg_dep_delay FROM flights_enriched WHERE FL_DATE BETWEEN '2025-02-01' AND '2025-02-29' GROUP BY ORIGIN ORDER BY COUNT(*) DESC LIMIT 1"
    },
    {
        "id": "16",
        "question": "2025年第一季度，到达延误（ARR_DELAY）超过60分钟的航班占当时总航班量的百分比是多少？",
        "expected_sql": "SELECT CAST(SUM(CASE WHEN ARR_DELAY > 60 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) * 100 FROM flights_enriched WHERE FL_DATE BETWEEN '2025-01-01' AND '2025-03-31'"
    },
    {
        "id": "17",
        "question": "列出最近30天，既没有降水（prcp_ORIGIN等于0或为空），且出发没有延误（DEP_DELAY <= 0）的航班数量。",
        "expected_sql": "SELECT COUNT(*) FROM flights_enriched WHERE COALESCE(prcp_ORIGIN, 0) = 0 AND COALESCE(DEP_DELAY, 0) <= 0 AND FL_DATE >= date((SELECT MAX(FL_DATE) FROM flights_enriched), '-30 day')"
    },
    {
        "id": "18",
        "question": "2025年1月，哪个航司取消航班的总数最多？请给出航司代码和取消数。",
        "expected_sql": "SELECT OP_UNIQUE_CARRIER, SUM(CANCELLED) AS total_cancelled FROM flights_enriched WHERE FL_DATE BETWEEN '2025-01-01' AND '2025-01-31' GROUP BY OP_UNIQUE_CARRIER ORDER BY total_cancelled DESC LIMIT 1"
    },
    {
        "id": "19",
        "question": "比较有出发延误和没有出发延误（DEP_DELAY > 0 vs <= 0）的航班，它们的平均到达延误时间分别是多少？",
        "expected_sql": "SELECT CASE WHEN COALESCE(DEP_DELAY, 0) > 0 THEN '有出发延误' ELSE '无出发延误' END AS dep_status, AVG(ARR_DELAY) AS avg_arr_delay FROM flights_enriched GROUP BY dep_status"
    }
]

def main():
    if not os.getenv("DASHSCOPE_API_KEY"):
        print("错误：未设置 DASHSCOPE_API_KEY，请在运行前设置环境变量。")
        sys.exit(1)

    config = load_config()
    db_path = str(Path(__file__).parent.parent / config["database"]["path"])
    
    # 初始化 LLM (遵循 app.py 的一致配置)
    llm = ChatOpenAI(
        model=config["llm"]["model"],
        api_key=config["llm"]["api_key"],
        base_url=config["llm"].get("base_url", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        temperature=0.01,  # 测试时降低随机性
        max_tokens=config["llm"]["max_tokens"],
    )
    
    agent = SQLQueryAgent(llm=llm, db_path=db_path, num_examples=2)
    
    success_count = 0
    total = len(GOLDEN_DATASET)
    
    print("=" * 50)
    print("🛫 NL2SQL Golden Dataset Benchmark (EX Accuracy)")
    print("=" * 50)
    
    for item in GOLDEN_DATASET:
        print(f"\n[测试用例 {item['id']}] {item['question']}")
        
        # 1. 运行 Agent 生成并执行
        agent_res = agent.query(item["question"], max_retries=3)
        if agent_res.get("error"):
            print(f"❌ 失败 (Agent 报错): {agent_res['error']}")
            continue
            
        generated_sql = agent_res.get("sql")
        agent_data_str = agent_res.get("data", "[]")
        
        # 2. 运行 Ground Truth 获取数据
        true_data = execute_sql_locally(db_path, item["expected_sql"])
        
        # 3. 数据集校验 (Execution Accuracy)
        agent_set = normalize_results(agent_data_str)
        true_set = normalize_results(true_data)
        
        # 如果长度一致，且交集等于总集，则可以认定执行结果一致
        if agent_set == true_set:
            success_count += 1
            print(f"✅ 通过 (Execution Match)")
        else:
            print(f"❌ 失败 (Data mismatch)")
            print(f"   预期 SQL: {item['expected_sql']}")
            print(f"   生成 SQL: {generated_sql}")
            
    print("\n" + "=" * 50)
    acc = (success_count / total) * 100
    print(f"📊 评测完成！Execution Accuracy (EX): {acc:.2f}% ({success_count}/{total})")
    print("=" * 50)

if __name__ == "__main__":
    main()
