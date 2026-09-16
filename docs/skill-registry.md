# Skill Registry 设计

## 设计目标

这个项目需要的是“轻量能力注册表”，不是复杂插件系统。目标是把现有能力用统一方式声明出来，让面试官能看到工具边界、权限要求和可扩展性。

当前实现包括：

- 声明文件：[config/skill_registry.yaml](../config/skill_registry.yaml)
- Registry loader：[tools/skill_registry.py](../tools/skill_registry.py)
- 技能薄封装：[tools/operational_skills.py](../tools/operational_skills.py)
- 查看接口：`GET /api/skills`

## 为什么不用复杂插件系统

生产级插件系统通常要处理安装、隔离、版本、权限授权、沙箱、生命周期和远程依赖。当前项目的面试目标是展示 Agent 工程化思路，所以只保留必要部分：

- 名称和描述
- 输入输出 schema
- 权限声明
- 调用入口
- 本地 wrapper

主编排仍由 LangGraph MasterAgent 控制，不允许工具自由自发运行。

## 当前注册能力

### SQLTool

只读 SQL 查询工具。支持两种模式：

- 通过 SQLQueryAgent 执行自然语言问题，包含 NL2SQL 和 Reflection。
- 直接执行只读 SELECT/WITH SQL，用于 deterministic skill 或评测。

权限：`database:read`

### SearchTool

外部资料搜索工具。复用 WebSearchAgent，对行业基准、FAA/BTS/公开报告等做检索、领域过滤和证据质量判断。

权限：`network:search`

### ChartTool

从表格数据生成轻量 ECharts option。当前是 deterministic wrapper；主流程中的复杂图表仍由 DataAnalysisAgent 生成。

权限：`local:compute`

### AnomalySkill

扫描最近周期和上一周期的指标差异，支持：

- `arr_delay_rate`
- `cancel_rate`
- `avg_arr_delay`

维度支持：

- `ORIGIN`
- `DEST`
- `OP_UNIQUE_CARRIER`

权限：`database:read`

### WeatherImpactSkill

按降水阈值拆分高/低天气暴露桶，对比：

- 平均到达延误
- 平均出发延误
- 到达延误率
- 取消率

权限：`database:read`

### ReportSkill

生成 deterministic 日报/周报摘要，包括航班量、平均延误、延误率、取消率和改降率。

权限：`database:read`

## 示例

```python
from tools.skill_registry import load_skill_registry
from tools.operational_skills import AnomalySkill, WeatherImpactSkill, ReportSkill

registry = load_skill_registry("config/skill_registry.yaml")
print(registry.summary()["count"])

db = "data/flight_weather.db"
print(AnomalySkill(db).scan(metric="arr_delay_rate", dimension="ORIGIN", limit=3))
print(WeatherImpactSkill(db).analyze(precipitation_threshold=2.0))
print(ReportSkill(db).generate(period="weekly")["report"])
```

## 面试讲法

可以这样解释：

> 我没有把它做成过度复杂的插件系统，而是做了一个轻量 Skill Registry。它把工具名称、输入输出、权限和入口声明出来，实际执行仍由 MasterAgent 编排。这样既能说明 Agent 的工具治理边界，又不会牺牲当前项目可跑性。

## 后续演进

- 给每个 skill 增加超时、重试和审计日志。
- 在 ToolRouterAgent 中读取 registry，而不是硬编码工具名。
- 将实时航班/天气 API 作为新 tool 注册。
- 在评测脚本中按 registry 遍历工具做 smoke test。
