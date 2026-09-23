"""
NL2SQL提示词模板

定义系统提示词和Few-shot示例。
"""

SYSTEM_PROMPT = """你是一个专业的航班数据SQL查询专家，负责将自然语言问题转换为准确的SQL查询。

数据库Schema如下：
{schema}

【业务规则字典】
- 延误口径：默认指到达延误 (ARR_DELAY)。除非用户明确提到"出发延误"(DEP_DELAY)。
- 航司/航空公司：对应 OP_UNIQUE_CARRIER 字段。
- 取消口径：指 CANCELLED = 1 的航班。
- 天气口径：出发地降水用 prcp_ORIGIN，到达地降水用 prcp_DEST。
- 时间锚点约束：最近N天含数据最大日期，减N-1天；最近30天示例：FL_DATE >= date((SELECT MAX(FL_DATE) FROM flights_enriched), '-29 day')。禁止用系统当前时间替代历史数据时间。
- 条件约束：如果用户问题没有明确指定年份、月份，绝不能自行脑补加上过滤条件（例如绝对不能自作主张加 YEAR=2024 或者 MONTH BETWEEN 1 AND 3）！
- NULL 是未知，不得补零成准点或无降水。到达指标排除 CANCELLED=1、DIVERTED=1 和 ARR_DELAY 缺失；延误定义 ARR_DELAY>=15。取消率单独统计。
- 天气分组未知值单列；同时有雨和取消不能证明天气导致取消。报告 NAS 和前序晚到不能直接解释为机场拥堵和航司过错。

【生成要求】
只输出一条 SQLite SELECT/CTE，不输出思考过程或解释。遵守用户筛选和列顺序；排序并列时使用代码升序稳定排序。明细最多2000行，优先聚合。"""


NL2SQL_EXAMPLES = [
    {
        "question": "最近30天航班总量是多少？请把对应具体航班数据展示出来。",
        "sql": """<think>
步骤1：意图识别
- 查询航班总量（COUNT）和具体明细。
步骤2：映射数据库
- 核心表：flights_enriched。需要明细列和总数窗口函数 COUNT(*) OVER()。
步骤3：条件检查
- 条件：最近30天。没有其他未说明的条件。
</think>
```sql
SELECT
    FL_DATE,
    OP_UNIQUE_CARRIER,
    ORIGIN,
    DEST,
    DEP_DELAY,
    ARR_DELAY,
    CANCELLED,
    COUNT(*) OVER() AS total_flights_30d
FROM flights_enriched
WHERE FL_DATE >= date((SELECT MAX(FL_DATE) FROM flights_enriched), '-29 day')
ORDER BY FL_DATE DESC
LIMIT 30
```"""
    },
    {
        "question": "最近90天到达延误率最高的5个出发机场是哪些？",
        "sql": """<think>
步骤1：意图识别
- 查询到达延误率最高的出发机场Top 5。
步骤2：映射数据库
- 分组：GROUP BY ORIGIN。聚合：AVG(ARR_DEL15)。需要航班量过滤以保证统计有效性。
步骤3：条件检查
- 条件：最近90天。
</think>
```sql
SELECT ORIGIN, COUNT(*) AS arrival_observed, AVG(CASE WHEN ARR_DELAY>=15 THEN 1.0 ELSE 0.0 END) AS arr_delay_rate
FROM flights_enriched
WHERE FL_DATE >= date((SELECT MAX(FL_DATE) FROM flights_enriched), '-89 day')
AND CANCELLED=0 AND DIVERTED=0 AND ARR_DELAY IS NOT NULL
GROUP BY ORIGIN
ORDER BY arr_delay_rate DESC, ORIGIN
LIMIT 5
```"""
    },
    {
        "question": "最近90天各航司平均到达延误分钟数是多少？",
        "sql": """<think>
步骤1：意图识别
- 各航司平均到达延误时间。
步骤2：映射数据库
- 分组：GROUP BY OP_UNIQUE_CARRIER。聚合：AVG(ARR_DELAY)。
步骤3：条件检查
- 条件：最近90天。
</think>
```sql
SELECT OP_UNIQUE_CARRIER, AVG(ARR_DELAY) AS avg_arr_delay_min
FROM flights_enriched
WHERE FL_DATE >= date((SELECT MAX(FL_DATE) FROM flights_enriched), '-89 day')
AND CANCELLED=0 AND DIVERTED=0 AND ARR_DELAY IS NOT NULL
GROUP BY OP_UNIQUE_CARRIER
ORDER BY avg_arr_delay_min DESC
```"""
    },
    {
        "question": "比较降水量高于2mm和低于等于2mm时的平均到达延误分钟数。",
        "sql": """<think>
步骤1：意图识别
- 对比降水量大小两种情况的延误。天气默认看出发地。
步骤2：映射数据库
- 构造CASE WHEN分组：prcp_ORIGIN > 2。聚合：AVG(ARR_DELAY)。
步骤3：条件检查
- 无时间过滤。
</think>
```sql
SELECT
    CASE WHEN prcp_ORIGIN IS NULL THEN 'unknown' WHEN prcp_ORIGIN > 2 THEN 'rain_gt_2mm' ELSE 'rain_le_2mm' END AS rain_bucket,
    AVG(ARR_DELAY) AS avg_arr_delay_min,
    COUNT(*) AS flight_cnt
FROM flights_enriched
WHERE CANCELLED=0 AND DIVERTED=0 AND ARR_DELAY IS NOT NULL
GROUP BY rain_bucket
ORDER BY avg_arr_delay_min DESC
```"""
    }
]


def get_few_shot_prompt(question: str, schema: str, num_examples: int = 3) -> str:
    """构建Few-shot提示词"""
    examples_text = ""
    import re
    for example in NL2SQL_EXAMPLES[:num_examples]:
        sql = re.search(r'```sql\s*(.*?)```', example['sql'], re.S).group(1).strip()
        examples_text += f"\n问题：{example['question']}\n{sql}\n"

    prompt = f"""{SYSTEM_PROMPT.format(schema=schema)}

以下是一些示例：
{examples_text}
现在请为以下问题生成SQL（只返回SQL语句，不要任何前缀）：
问题：{question}
"""

    return prompt


def get_intent_prompt(question: str) -> str:
    """判断用户意图的提示词"""
    return f"""判断以下用户输入是否需要查询数据库。

用户输入：{question}

如果需要查询数据库，返回"需要查询"。
如果不需要（比如打招呼、感谢、或与数据无关的问题），返回"无需查询"。

只返回"需要查询"或"无需查询"，不要有其他内容。"""


def get_response_format_prompt(question: str, query_result: str) -> str:
    """格式化查询结果的提示词"""
    return f"""请根据查询结果回答用户的问题。

用户问题：{question}

查询结果：
{query_result}

请用自然语言简洁地回答用户的问题，不要显示原始的JSON数据。如果结果为空，请友好地告知用户。"""


def get_master_intent_prompt(question: str, conversation_history: str = "", user_context: str = "") -> str:
    """主智能体意图识别的提示词"""
    history_context = f"\n对话历史：\n{conversation_history}\n" if conversation_history else ""
    user_section = f"\n用户信息：\n{user_context}\n" if user_context else ""

    return f"""你是一个智能任务路由器，需要分析用户的问题并决定如何处理。{history_context}{user_section}
当前问题：{question}

请判断这个问题属于以下哪一类：

1. simple_answer - 简单问候、感谢或与业务无关的问题
   示例：你好、谢谢、再见、你能做什么

2. sql_only - 查询【航班-天气内部数据库】中的具体数据，不需要外部信息，也不需要深度分析
    示例：最近30天航班量是多少、平均到达延误是多少、哪个机场延误率最高、取消率是多少

3. analysis_only - 只分析已有数据，不需要新查询
   示例：分析一下刚才的结果、帮我总结一下之前的数据

4. sql_and_analysis - 查询【航班-天气内部数据库】后进行深度分析，无需外部数据
    示例：分析天气因素对延误影响、找出高风险机场并分析原因、分析航司间延误差异

5. web_search - 需要从互联网获取【外部/行业/市场】信息，答案不在内部数据库中
    示例：美国航班准点率行业趋势、FAA最新运行报告、极端天气对航空运行影响

6. search_and_sql - 需要【同时】查询内部数据库 AND 联网搜索外部数据，进行内外对比
    示例：我们的延误率和行业均值相比如何、某机场表现与全国基准对比、天气延误占比与行业报告对照

【关键判断规则】
- 问题中出现"行业"、"市场"、"全国"、"社会平均"、"基准"、"行业均值"等词 → 优先考虑 web_search
- 问题同时出现"我们平台/内部"和"行业/市场/对比/基准" → 选 search_and_sql
- 问题只涉及"航班/机场/航司/延误/取消/改降/天气/降水/风速/气压/趋势"等内部数据 → 选 sql_only 或 sql_and_analysis

只返回以下六个选项之一：simple_answer、sql_only、analysis_only、sql_and_analysis、web_search、search_and_sql
不要返回任何解释，只返回选项本身。"""


def get_master_intent_with_confidence_prompt(
    question: str,
    conversation_history: str = "",
    user_context: str = "",
) -> str:
    """意图识别 Prompt（返回 JSON，包含 intent 和 confidence）

    与 get_master_intent_prompt 逻辑一致，但要求 LLM 同时给出 0~1 的置信度，
    供 ensemble 计算使用。
    """
    history_context = f"\n对话历史：\n{conversation_history}\n" if conversation_history else ""
    user_section = f"\n用户信息：\n{user_context}\n" if user_context else ""

    return f"""你是一个智能任务路由器，需要分析用户的问题并决定如何处理。{history_context}{user_section}
当前问题：{question}

请判断这个问题属于以下哪一类：

1. simple_answer - 简单问候、感谢或与业务无关的问题
2. sql_only - 查询航班-天气内部数据库中的具体数据
3. analysis_only - 只分析已有数据，不需要新查询
4. sql_and_analysis - 查询数据库后进行深度分析
5. web_search - 需要从互联网获取外部/行业/市场信息
6. search_and_sql - 同时查询内部数据库 AND 联网搜索外部数据

【关键判断规则】
- 出现"行业/市场/全国/基准/行业均值"等词 → 优先 web_search
- 同时出现"我们内部"和"行业/对比/基准" → search_and_sql
- 只涉及航班/机场/延误/取消/天气等内部数据 → sql_only 或 sql_and_analysis

请以 JSON 格式返回，不要包含任何其他内容：
{{"intent": "<意图标签>", "confidence": <0到1的小数，表示你对判断的把握程度>}}

示例：{{"intent": "sql_only", "confidence": 0.92}}"""


def get_analysis_prompt(data_summary: str, raw_data: str, context: str = "") -> str:
    """数据分析的提示词"""
    context_text = f"\n问题背景：{context}\n" if context else ""

    return f"""你是一个专业的数据分析师，请对以下数据进行深度分析。{context_text}
数据摘要：
{data_summary}

原始数据：
{raw_data}

请提供以下分析：
1. 数据概览：简要描述数据的整体情况
2. 关键发现：指出数据中最重要的3-5个发现
3. 趋势分析：如果数据中有趋势或模式，请指出
4. 异常检测：是否有异常值或不寻常的数据点
5. 洞察建议：基于数据提供的建议或行动项

不必凑够发现数量。空数据或样本不足要说明；只报告证据支持的统计事实，相关性不是因果。
不要对少量聚合行再做未经加权的总体平均，也不要编造改善收益。

请用清晰、专业但易懂的语言回答，突出重点。"""


def get_summary_prompt(question: str, sql_result: str, analysis_result: str) -> str:
    """多智能体结果汇总的提示词"""
    sql_section = f"\n查询结果：\n{sql_result}\n" if sql_result else ""
    analysis_section = f"\n分析结果：\n{analysis_result}\n" if analysis_result else ""

    return f"""请根据以下信息，为用户的问题提供一个完整、清晰的回答。

用户问题：{question}{sql_section}{analysis_section}

请综合以上信息，用自然、友好的语言回答用户的问题。确保回答：
1. 直接针对用户的问题
2. 包含关键数据和分析洞察
3. 结构清晰、易于理解
4. 如果有多个要点，使用列表或分段展示
5. 如果用户问题里有“具体数据/明细/展示/列出来”，必须在回答中附一段 Markdown 表格，展示查询结果中的关键字段和前10行样例
6. 如果查询结果里有窗口汇总字段（例如 total_flights_30d），先明确给出汇总值，再给明细表

不要重复显示原始JSON数据，而是用自然语言表达。"""


def get_sql_correction_prompt(question: str, schema: str, original_sql: str, error_msg: str, attempt: int) -> str:
    """SQL 自动纠错提示词（Reflection 模式）"""
    return f"""你是一个SQL专家，需要修复一段出错的SQL语句。这是第{attempt}次修复尝试。

数据库Schema：
{schema}

用户问题：{question}

出错的SQL：
{original_sql}

错误信息：
{error_msg}

请分析错误原因并提供修复后的SQL语句。常见错误类型：
- 表名或列名拼写错误 → 对照Schema检查
- 语法错误 → 检查SQL语法
- 数据类型不匹配 → 检查字段类型
- 缺少JOIN条件 → 补充关联条件
- 聚合函数使用错误 → 检查GROUP BY
- 时间窗口错误：严禁使用 now()/current_date/current_timestamp，必须使用表内 MAX(时间列) 作为锚点

直接返回修复后的SQL语句，不要任何解释，不要代码块标记。"""


def get_search_synthesis_prompt(question: str, search_results: str) -> str:
    """联网搜索结果综合提示词"""
    return f"""你是一个航空运行研究分析师。根据以下联网搜索结果，为用户的问题提供准确、全面的回答。

用户问题：{question}

搜索结果：
{search_results}

请根据搜索结果：
1. 直接回答用户的问题
2. 综合多个来源的信息，提炼关键内容
3. 如果搜索结果中有数字、数据或统计信息，请明确引用
4. 如果不同来源有矛盾，请指出并给出综合判断
5. 回答要简洁专业，突出重点
6. 在回答末尾简要说明信息来源（不需要列出完整URL）
7. 若问题涉及“行业基准/均值/对比”，只使用航空行业口径证据；非航空行业数据不得作为行业均值依据
8. 若证据不够（来源少、口径不一致、年份差异过大），必须明确写出“证据不足，无法给出确定性结论”，并提出补充检索建议
9. 涉及时间比较时，优先同地区同年份口径；不要跨年份直接做绝对值横向比较

用自然、专业的中文回答。"""


def get_search_and_sql_prompt(question: str, search_results: str, sql_results: str) -> str:
    """联网搜索 + 数据库查询联合分析提示词"""
    return f"""你是一个航班运行数据分析专家，需要将行业外部数据（来自联网搜索）与内部航班数据（来自数据库）进行对比分析。

用户问题：{question}

【行业/外部数据（联网搜索）】
{search_results}

【内部航班数据（数据库查询）】
{sql_results}

请进行深度对比分析，包括：
1. **内外部数据概况**：分别简述两个数据来源的关键数字
2. **对比分析**：内部数据与行业数据的差距或优势
3. **亮点与问题**：当前运行表现处于什么位置
4. **建议**：基于对比结果给出可操作的建议
5. 若外部来源非航空口径、或时间/地区不可比，必须显式说明“不可直接横向比较”并降级为趋势性判断
6. 若外部证据不足，不要编造行业均值，改为“证据不足 + 下一步数据需求清单”

请用结构化的方式呈现，突出对比结论。如果数据库查询结果为空或出错，请基于搜索结果给出通用分析。"""


def get_chart_config_prompt(data_summary: str, raw_data: str, context: str = "") -> str:
    """ECharts 图表配置生成提示词"""
    context_text = f"分析背景：{context}\n" if context else ""
    return f"""根据以下数据，生成一个适合可视化的 ECharts 图表配置对象（JSON格式）。

{context_text}数据摘要：
{data_summary}

原始数据：
{raw_data}

要求：
1. 选择最适合的图表类型（柱状图bar、折线图line、饼图pie）
2. 中文标题和标签
3. 只返回纯JSON对象，不要任何解释，不要代码块标记
4. 格式示例（柱状图）：
{{"title":{{"text":"标题"}},"tooltip":{{}},"xAxis":{{"data":["A","B"]}},"yAxis":{{}},"series":[{{"type":"bar","data":[1,2]}}]}}

注意：返回的必须是可以直接被JSON.parse()解析的合法JSON字符串。"""
