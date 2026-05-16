# 多智能体航班-天气分析系统 v3.2

基于 LangGraph 的多智能体数据分析系统，面向航班运行与天气影响分析场景。

## 当前默认数据

- 数据库路径：`data/flight_weather.db`
- 主表：`flights_enriched`
- KPI视图：`v_flight_monthly_kpi`
- 来源CSV：`data/Flight_Data_With_Weather_Final.csv`

## 核心能力

- 自然语言转 SQL（NL2SQL）
- SQL 纠错重试（Reflection）
- 结构化数据分析 + 图表生成（ECharts）
- 联网搜索与行业基准对比
- Guardrail 安全拦截
- Critic 结构化审校
- Debate 评分裁决与可信度分级
- 长短期记忆

## 快速开始

1. 安装依赖

```bash
pip install -r requirements.txt
```

2. 设置 API Key

```bash
# Windows PowerShell
$env:DASHSCOPE_API_KEY = "your_key"
$env:TAVILY_API_KEY = "your_tavily_key"   # 可选
```

3. 初始化航班数据库（已准备 CSV 时）

```bash
python data/init_flight_weather_db.py
```

4. 启动 Web

```bash
start_web.bat
```

访问 `http://localhost:5000`

## 推荐测试问题

- 最近30天航班总量是多少？
- 最近90天到达延误率最高的10个出发机场是哪些？
- 最近90天取消率最高的5家航司是哪些？
- 比较降水量高于2mm和低于等于2mm时的平均到达延误分钟数。
- 结合行业基准，对比我们近90天到达延误率并给出优化建议。

## 目录

- `agents/`：多智能体实现
- `data/`：数据与数据库初始化脚本
- `static/`：前端页面
- `config/config.yaml`：系统配置
- `app.py`：Flask API 服务
- `mcp_sql_server.py`：MCP SQL 执行服务

## 说明

旧版电商/Olist 文案与示例已迁移为航班-天气场景；如果你仍需切回旧数据，请把 `config/config.yaml` 中 `database.path` 指回对应数据库。
