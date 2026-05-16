# 🚀 项目：全局意图识别优化（Multi‑Agent for NLP）

## 1️⃣ 背景概述
当前系统的意图识别全部依赖 **LLM 直接返回文字**（`MasterAgent._intent_node`），没有显式置信度、规则备份或用户确认机制。  
这导致：

* 高频、明确的意图（如 “查询数量”“前 5 名”“天气”）仍走模型路径，产生不必要的随机性。  
* 当模型返回不在合法意图集合时，需要手动纠正，错误率提升。  
* 缺少 **置信度阈值 + 交互确认**，用户在模糊问题上得不到系统提示。  
* 未来要实现 **模型 + 规则混合、ensemble、持续改进**，需要一套可追踪的实现路线。

本计划把意图识别拆分为 **规则 → 轻量分类模型 → 可选 embedding ensemble → 交互式确认** 四个层次，并在每一步完成后在此文档中勾选进度，确保透明可追踪。

---

## 2️⃣ 目标（Target Outcomes）

| 编号 | 目标 | 评估方式 |
|-----|------|----------|
| **T1** | **高频明确意图**（数量、比例、TOP K、天气）通过规则匹配，置信度 1.0，直接路由 | 单元测试匹配 100% |
| **T2** | **轻量分类模型** 能对剩余意图进行 **≥90%** 的准确率预测，并输出概率置信度 | 交叉验证 / 手动标注测试集 |
| **T3** | **置信度阈值**（默认 0.75）低于阈值时返回 `ask_user=True`，前端可弹窗确认 | 调用 `MultiAgentSystem.query` 并检查返回结构 |
| **T4** | **可选 Embedding Ensemble**（TF‑IDF + sentence‑transformers）提升置信度评分的鲁棒性（可配置开关） | A/B 对比置信度分布 |
| **T5** | **错误案例收集**：所有意图识别错误写入 `logs/error_cases.json` 供后续 fine‑tune | 观察日志文件大小、记录条数 |
| **T6** | **完整文档**：每一步完成后在本 MD 中勾选 ✅，并记录关键代码/提交 SHA | 手动在本文件勾选 |

---

## 3️⃣ 执行计划（Step‑by‑Step Implementation)

> *每一步完成后，打开 `plan_intent_optimization.md`，在对应任务前的 `[ ]` 改为 `[x]` 并写上提交 SHA（或日期）*  

| 步骤 | 子任务 | 关键文件/位置 | 预计工时 | 完成标记 |
|------|--------|----------------|----------|-----------|
| **1️⃣ 需求梳理 & 环境准备** | - 阅读 `master_agent.py` `_intent_node` 实现 <br>- 在 `requirements.txt` 添加 `scikit-learn`（已存在） <br>- 创建 `intent.py`（枚举） 与 `intent_classifier.py`（轻量模型） | `agents/master_agent.py` <br>`intent.py` <br>`intent_classifier.py` | 0.3 天 | `[ ]` |
| **2️⃣ 规则匹配实现** | - 在 `intent_classifier.py` 编写 `_rule_intent(question)` 正则库 <br>- 列出 6‑8 高频关键词（数量、比例、TOP K、天气、天气‑查询） <br>- 返回 `(Intent, 1.0)` | `intent_classifier.py` | 0.4 天 | `[ ]` |
| **3️⃣ 轻量模型训练** | - 采集训练数据：从 `GOLDEN_DATASET` + 手动标注 30 条额外示例（覆盖 `simple_answer`, `sql_only`, `analysis_only`, `sql_and_analysis`, `web_search`, `search_and_sql`） <br>- 保存为 `data/intent_train.json` <br>- 使用 `TfidfVectorizer + LinearSVC` 训练 <br>- 实现 `predict_intent(question) → (Intent, confidence)` <br>- 添加模型 **lazy loading**（首次调用加载） | `intent_classifier.py` <br>`data/intent_train.json` | 0.8 天 | `[ ]` |
| **4️⃣ 集成到 MasterAgent** | - 在 `MasterAgent._intent_node` 前调用 `IntentClassifier.classify(question)` <br>- 若规则匹配成功直接返回；否则使用模型返回 `(intent, conf)` <br>- 将 `conf` 写入 `state["metadata"]["intent_confidence"]` <br>- 若 `conf < THRESHOLD`（默认 0.75）返回结构 `{ "ask_user": True, "clarification": "...", "original_question": q }` <br>- 更新 `_route_after_intent` 读取 `state["metadata"]["intent_confidence"]`，不影响现有路径 | `agents/master_agent.py` | 0.6 天 | `[ ]` |
| **5️⃣ 前端/CLI 交互** | - 在 `app.py`（CLI）检测返回的 `dict` 是否包含 `ask_user`，若是则打印确认提示并等待用户输入（`y/n`） <br>- 若用户确认，重新调用 `system.query` 并强制使用返回的 `intent`（可通过 `metadata["forced_intent"]` 传递） <br>- 对 Flask/前端（若存在）同理，返回 JSON `{ask_user:true, clarification:...}` 让前端弹框 | `app.py` <br>`agent.py`（如有 HTTP 接口） | 0.4 天 | `[ ]` |
| **6️⃣ Embedding Ensemble（可选）** | - 安装 `sentence-transformers`（轻量模型 `all-MiniLM-L6-v2`） <br>- 为每个 Intent 预计算 **prototype 向量**（使用训练集的平均向量） <br>- 在 `IntentClassifier.classify` 中计算 **cosine 相似度** 与模型置信度加权 <br>- 通过 `config.yaml` 开关 `intent_ensemble: true/false` | `intent_classifier.py` <br>`config/config.yaml` | 1 天（可推迟） | `[ ]` |
| **7️⃣ 错误案例日志** | - 在 `MasterAgent._intent_node` 捕获异常或置信度低于阈值的 **误判**，写入 `logs/error_cases.json`（结构：`{question, llm_output, predicted_intent, confidence, error_type}`） <br>- 为 `logs/` 目录添加 `.gitkeep`（防止空目录） | `agents/master_agent.py` | 0.2 天 | `[ ]` |
| **8️⃣ 单元/集成测试** | - 为 `intent_classifier.py` 编写 `pytest` 用例（规则、模型、阈值） <br>- 为 `master_agent.py` 编写 **意图路由** 测试，确保 `ask_user` 分支可被捕获 <br>- 运行全链路基准 `python eval/nl2sql_golden_eval.py` 确认 SQL 部分不受影响 | `tests/`（新建） | 0.4 天 | `[ ]` |
| **9️⃣ 文档与交付** | - 在 `README.md` 增加 **意图识别** 部分说明 <br>- 更新 **CHANGELOG** <br>- 在本 MD 中记录每一步完成的 **Git commit SHA** 与 **日期** | `README.md` <br>`CHANGELOG.md` | 0.2 天 | `[ ]` |

### 总时间估算
| 项目 | 估计 (天) |
|------|-----------|
| 步骤 1‑5（核心） | **2.5 天** |
| 步骤 6（可选） | **1 天** |
| 步骤 7‑9（收尾） | **0.8 天** |
| **合计** | **≈3.3 天**（不含可选 ensemble） |

---

## 4️⃣ 进度监控（Progress Tracker)

> 在每次提交后打开此文件，将对应任务前的 `[ ]` 改为 `[x]` 并在括号内写上 **Git SHA**（或提交日期）。

| 步骤 | 任务 | 状态 |
|------|------|------|
| 1️⃣ | 需求梳理 & 环境准备 | [x] 2026-04-26 |
| 2️⃣ | 规则匹配实现 | [x] 2026-04-26 |
| 3️⃣ | 轻量模型训练 | [x] 2026-04-26 （改为 Embedding 方案，含原型句子 + DashScope text-embedding-v3） |
| 4️⃣ | 集成到 MasterAgent | [ ] |
| 5️⃣ | 前端/CLI 交互 | [ ] |
| 6️⃣ | Embedding Ensemble（可选） | [ ] |
| 7️⃣ | 错误案例日志 | [ ] |
| 8️⃣ | 单元/集成测试 | [ ] |
| 9️⃣ | 文档与交付 | [ ] |
