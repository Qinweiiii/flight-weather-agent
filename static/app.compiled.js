const {
  useEffect,
  useRef,
  useState
} = React;
const API_BASE_URL = window.location.origin;
const quickStarts = [{
  title: "原因与行动",
  body: "诊断最近30天的延误原因，列出优先复核对象和运营建议。",
  icon: "A"
}, {
  title: "取消异常筛查",
  body: "最近30天哪些机场取消率异常？与前30天对比。",
  icon: "C"
}, {
  title: "运营周报",
  body: "生成运营周报，说明有效观测和延误率口径。",
  icon: "W"
}, {
  title: "行业基准对比",
  body: "结合行业基准，对比我们近90天到达延误率并给出优化建议。",
  icon: "B"
}];
const navItems = [["Workbench", "工作台"], ["Memory", "记忆"], ["Trace", "追踪"], ["Skills", "技能"]];
function apiCall(endpoint, data = {}) {
  return fetch(`${API_BASE_URL}/api/${endpoint}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(data)
  }).then(async response => {
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "请求失败");
    return result;
  });
}
function renderMarkdown(text) {
  if (!text) return "";
  try {
    if (window.marked) {
      const raw = window.marked.parse(text, {
        gfm: true,
        breaks: true,
        mangle: false,
        headerIds: false
      });
      if (window.DOMPurify) return window.DOMPurify.sanitize(raw);
    }
  } catch (error) {
    console.warn("Markdown render failed:", error);
  }
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}
function uniqueId(prefix) {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}
function useSSEChat({
  userId,
  onMemoryRefresh
}) {
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const controllerRef = useRef(null);
  const busyRef = useRef(false);
  useEffect(() => () => controllerRef.current?.abort(), []);
  const sendQuestion = async question => {
    const trimmed = question.trim();
    if (!trimmed || busyRef.current) return;
    busyRef.current = true;
    controllerRef.current = new AbortController();
    setError("");
    setIsLoading(true);
    const userMessage = {
      id: uniqueId("user"),
      role: "user",
      text: trimmed,
      createdAt: new Date()
    };
    const assistantId = uniqueId("assistant");
    const assistantMessage = {
      id: assistantId,
      role: "assistant",
      text: "",
      status: "正在建立请求...",
      intent: "",
      traces: [],
      plan: null,
      sql: null,
      retryCount: 0,
      sources: [],
      quality: null,
      chart: null,
      error: "",
      createdAt: new Date()
    };
    setMessages(prev => [...prev, userMessage, assistantMessage]);
    const patchAssistant = patch => {
      setMessages(prev => prev.map(message => {
        if (message.id !== assistantId) return message;
        const nextPatch = typeof patch === "function" ? patch(message) : patch;
        return {
          ...message,
          ...nextPatch
        };
      }));
    };
    try {
      const response = await fetch(`${API_BASE_URL}/api/query_stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          user_id: userId,
          question: trimmed
        }),
        signal: controllerRef.current.signal
      });
      if (!response.ok) {
        const detail = await response.json();
        throw new Error(detail.error || `HTTP ${response.status}`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let fullAnswer = "";
      let receivedDone = false;
      while (true) {
        const {
          done,
          value
        } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {
          stream: true
        });
        const parts = buffer.split("\n\n");
        buffer = parts.pop();
        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data: ")) continue;
          const event = JSON.parse(line.slice(6));
          if (event.type === "status") patchAssistant({
            status: event.message || "处理中..."
          });
          if (event.type === "intent") patchAssistant({
            intent: event.intent || ""
          });
          if (event.type === "plan") patchAssistant({
            plan: event.plan || null
          });
          if (event.type === "trace") {
            patchAssistant(message => ({
              traces: [...(message.traces || []), {
                step: event.step || "trace",
                detail: event.detail || ""
              }]
            }));
          }
          if (event.type === "quality") patchAssistant(message => ({
            quality: {
              ...message.quality,
              ...event.quality
            }
          }));
          if (event.type === "decision") patchAssistant({
            decision: event
          });
          if (event.type === "sql") {
            patchAssistant({
              sql: event.sql || "",
              retryCount: event.retry_count || 0,
              sqlParams: event.params || []
            });
          }
          if (event.type === "sources") patchAssistant({
            sources: event.sources || []
          });
          if (event.type === "chart") patchAssistant({
            chart: event.config || null
          });
          if (event.type === "chunk") {
            fullAnswer += event.content || "";
            patchAssistant({
              text: fullAnswer,
              status: ""
            });
          }
          if (event.type === "error") {
            patchAssistant({
              error: event.message || "处理过程中出现错误",
              status: event.message || "处理过程中出现错误"
            });
          }
          if (event.type === "done") {
            receivedDone = true;
            if (event.answer !== undefined) {
              fullAnswer = event.answer;
              patchAssistant({
                text: fullAnswer
              });
            }
            patchAssistant({
              status: ""
            });
          }
        }
      }
      if (!receivedDone) throw new Error("连接提前结束，分析结果可能不完整，请重试。");
      if (onMemoryRefresh) onMemoryRefresh();
    } catch (err) {
      const message = err.name === "AbortError" ? "已停止接收。服务器正在结束当前步骤。" : err.message || "请求失败";
      setError(message);
      patchAssistant({
        error: message,
        status: ""
      });
    } finally {
      setIsLoading(false);
      busyRef.current = false;
    }
  };
  const resetMessages = (note = "") => {
    setMessages(note ? [{
      id: uniqueId("system"),
      role: "assistant",
      text: note,
      createdAt: new Date()
    }] : []);
  };
  return {
    messages,
    isLoading,
    error,
    sendQuestion,
    resetMessages,
    stop: () => controllerRef.current?.abort()
  };
}
function App() {
  const [user, setUser] = useState(null);
  const [userInfo, setUserInfo] = useState(null);
  const [activePanel, setActivePanel] = useState("Workbench");
  const [skills, setSkills] = useState([]);
  const [toast, setToast] = useState("");
  const [dataset, setDataset] = useState(null);
  const refreshUserInfo = async () => {
    if (!user?.userId) return;
    try {
      const result = await apiCall("user_info", {
        user_id: user.userId
      });
      if (result.success) setUserInfo(result.user_info);
    } catch (error) {
      console.warn("Failed to refresh user info:", error);
    }
  };
  const {
    messages,
    isLoading,
    error,
    sendQuestion,
    resetMessages,
    stop
  } = useSSEChat({
    userId: user?.userId,
    onMemoryRefresh: refreshUserInfo
  });
  useEffect(() => {
    fetch(`${API_BASE_URL}/api/dataset`).then(r => r.json()).then(setDataset).catch(() => setDataset({
      error: "数据状态不可用"
    }));
    fetch(`${API_BASE_URL}/api/skills`).then(res => res.json()).then(payload => setSkills(payload?.registry?.skills || [])).catch(() => setSkills([]));
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 3200);
    return () => clearTimeout(timer);
  }, [toast]);
  const handleLogin = async userId => {
    const result = await apiCall("login", {
      user_id: userId || "guest"
    });
    if (result.success) {
      setUser({
        userId: result.user_id,
        sessionId: result.session_id
      });
      setUserInfo(result.user_info || null);
      resetMessages("");
    }
  };
  const handleNewSession = async () => {
    if (!user) return;
    if (isLoading) {
      setToast("请等待当前分析结束");
      return;
    }
    try {
      const result = await apiCall("new_session", {
        user_id: user.userId
      });
      if (result.success) {
        setUser(prev => ({
          ...prev,
          sessionId: result.session_id
        }));
        resetMessages("");
        setToast("新会话已创建");
      }
    } catch (err) {
      setToast(err.message);
    }
  };
  const handleResetMemory = async () => {
    if (isLoading) {
      setToast("请等待当前分析结束");
      return;
    }
    if (!window.confirm("清除当前用户的长期记忆？其他用户不受影响。")) return;
    try {
      const result = await apiCall("reset_memory", {
        confirm: true,
        user_id: user.userId
      });
      if (result.success) {
        setUser(prev => ({
          ...prev,
          sessionId: result.session_id
        }));
        resetMessages("");
        await refreshUserInfo();
        setToast("当前用户记忆已清除");
      }
    } catch (err) {
      setToast(err.message);
    }
  };
  if (!user) return React.createElement(LoginPanel, {
    onLogin: handleLogin,
    dataset: dataset
  });
  return React.createElement("div", {
    className: "app-shell"
  }, React.createElement(Sidebar, {
    user: user,
    userInfo: userInfo,
    activePanel: activePanel,
    onPanelChange: setActivePanel,
    onNewSession: handleNewSession,
    onResetMemory: handleResetMemory,
    onLogout: () => window.location.reload(),
    skills: skills
  }), React.createElement("main", {
    className: "workspace"
  }, React.createElement(TopBar, {
    activePanel: activePanel,
    onPanelChange: setActivePanel,
    onNewSession: handleNewSession
  }), React.createElement(DatasetBanner, {
    dataset: dataset
  }), activePanel === "Memory" ? React.createElement(MemoryPanel, {
    userInfo: userInfo,
    onRefresh: refreshUserInfo,
    onReset: handleResetMemory
  }) : activePanel === "Skills" ? React.createElement(SkillPanel, {
    skills: skills
  }) : activePanel === "Trace" ? React.createElement("section", {
    className: "panel-page"
  }, React.createElement("h1", null, "\u6267\u884C\u8BB0\u5F55"), messages.filter(m => m.role === 'assistant').map(m => React.createElement(AgentTraceTimeline, {
    key: m.id,
    traces: m.traces,
    plan: m.plan,
    intent: m.intent,
    quality: m.quality
  }))) : React.createElement(ChatWindow, {
    user: user,
    messages: messages,
    isLoading: isLoading,
    error: error,
    onSend: sendQuestion,
    onQuickStart: sendQuestion,
    onStop: stop
  })), toast ? React.createElement("div", {
    className: "toast"
  }, toast) : null);
}
function DatasetBanner({
  dataset
}) {
  return React.createElement("div", {
    className: "dataset-banner",
    role: "status"
  }, dataset?.success ? `${dataset.mode === 'demo' ? '离线固定场景 · ' : '模型工作流 · '}${dataset.dataset.label} · ${dataset.dataset.min_date} — ${dataset.dataset.max_date} · ${dataset.dataset.flight_cnt.toLocaleString()} 条` : dataset?.error || '正在读取数据状态');
}
function LoginPanel({
  onLogin,
  dataset
}) {
  const [userId, setUserId] = useState("guest");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const submit = async event => {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      await onLogin(userId.trim() || "guest");
    } catch (err) {
      setError(err.message || "登录失败");
    } finally {
      setLoading(false);
    }
  };
  return React.createElement("div", {
    className: "login-stage"
  }, React.createElement("section", {
    className: "login-panel"
  }, React.createElement("div", {
    className: "window-dots",
    "aria-hidden": "true"
  }, React.createElement("span", null), React.createElement("span", null), React.createElement("span", null)), React.createElement("p", {
    className: "eyebrow"
  }, "Flight Operations Agentic BI"), React.createElement("h1", null, "\u822A\u7A7A\u8FD0\u8425\u8BCA\u65AD\u5DE5\u4F5C\u53F0"), React.createElement("p", {
    className: "login-copy"
  }, "\u5386\u53F2\u8FD0\u8425\u590D\u76D8\u3001\u5F02\u5E38\u7B5B\u67E5\u4E0E\u884C\u52A8\u9A8C\u8BC1\u3002"), React.createElement(DatasetBanner, {
    dataset: dataset
  }), React.createElement("form", {
    onSubmit: submit,
    className: "login-form"
  }, React.createElement("label", {
    htmlFor: "userId"
  }, "\u7528\u6237 ID"), React.createElement("div", {
    className: "inline-input"
  }, React.createElement("input", {
    id: "userId",
    value: userId,
    onChange: event => setUserId(event.target.value),
    placeholder: "guest"
  }), React.createElement("button", {
    type: "submit",
    disabled: loading
  }, loading ? "进入中" : "进入")), error ? React.createElement("div", {
    className: "form-error"
  }, error) : null)));
}
function Sidebar({
  user,
  userInfo,
  activePanel,
  onPanelChange,
  onNewSession,
  onResetMemory,
  onLogout,
  skills
}) {
  const preferenceCount = userInfo?.preferences ? Object.keys(userInfo.preferences).length : 0;
  const knowledgeCount = Array.isArray(userInfo?.knowledge) ? userInfo.knowledge.length : 0;
  return React.createElement("aside", {
    className: "sidebar"
  }, React.createElement("div", {
    className: "window-dots",
    "aria-hidden": "true"
  }, React.createElement("span", null), React.createElement("span", null), React.createElement("span", null)), React.createElement("button", {
    className: "nav-command",
    onClick: onNewSession
  }, React.createElement("span", null, "+"), " \u65B0\u4F1A\u8BDD"), React.createElement("nav", {
    className: "nav-list"
  }, navItems.map(([key, label]) => React.createElement("button", {
    key: key,
    className: activePanel === key ? "active" : "",
    onClick: () => onPanelChange(key)
  }, React.createElement("span", {
    className: "nav-icon"
  }, key.slice(0, 1)), label))), React.createElement("div", {
    className: "sidebar-section"
  }, React.createElement("div", {
    className: "section-title"
  }, "\u5206\u6790\u4E3B\u9898"), React.createElement("div", {
    className: "recent-item"
  }, React.createElement("strong", null, "\u5EF6\u8BEF\u7387\u5F02\u5E38\u8BCA\u65AD"), React.createElement("span", null, "\u673A\u573A\u3001\u822A\u53F8\u3001\u5929\u6C14\u56E0\u7D20\u8054\u5408\u5206\u6790")), React.createElement("div", {
    className: "recent-item"
  }, React.createElement("strong", null, "\u884C\u4E1A\u57FA\u51C6\u5BF9\u6BD4"), React.createElement("span", null, "\u5185\u90E8 SQL \u8BC1\u636E\u4E0E\u5916\u90E8\u8D44\u6599\u88C1\u51B3")), React.createElement("div", {
    className: "recent-item"
  }, React.createElement("strong", null, "\u8FD0\u8425\u5468\u62A5\u6458\u8981"), React.createElement("span", null, "\u822A\u73ED\u91CF\u3001\u53D6\u6D88\u7387\u3001\u6539\u964D\u7387\u590D\u76D8"))), React.createElement("div", {
    className: "sidebar-section compact"
  }, React.createElement("div", {
    className: "section-title"
  }, "System"), React.createElement("div", {
    className: "metric-row"
  }, React.createElement("span", null, "Skills"), React.createElement("strong", null, skills.length)), React.createElement("div", {
    className: "metric-row"
  }, React.createElement("span", null, "Preferences"), React.createElement("strong", null, preferenceCount)), React.createElement("div", {
    className: "metric-row"
  }, React.createElement("span", null, "Knowledge"), React.createElement("strong", null, knowledgeCount)), React.createElement("button", {
    className: "quiet-action",
    onClick: onResetMemory
  }, "\u91CD\u7F6E\u8BB0\u5FC6")), React.createElement("div", {
    className: "sidebar-user"
  }, React.createElement("div", {
    className: "avatar"
  }, (user.userId || "G").slice(0, 1).toUpperCase()), React.createElement("div", null, React.createElement("strong", null, user.userId), React.createElement("span", null, user.sessionId ? `${user.sessionId.slice(0, 8)}...` : "-")), React.createElement("button", {
    className: "icon-button",
    onClick: onLogout,
    title: "\u9000\u51FA"
  }, "\u2197")));
}
function TopBar({
  activePanel,
  onPanelChange,
  onNewSession
}) {
  return React.createElement("header", {
    className: "topbar"
  }, React.createElement("button", {
    className: "icon-button mobile-new",
    title: "\u65B0\u4F1A\u8BDD",
    "aria-label": "\u65B0\u4F1A\u8BDD",
    onClick: onNewSession
  }, "+"), React.createElement("div", {
    className: "segmented",
    "aria-label": "workspace sections"
  }, React.createElement("button", {
    className: activePanel === "Workbench" ? "active" : "",
    onClick: () => onPanelChange("Workbench")
  }, "Workbench"), React.createElement("button", {
    className: activePanel === "Trace" ? "active" : "",
    onClick: () => onPanelChange("Trace")
  }, "Trace"), React.createElement("button", {
    className: activePanel === "Skills" ? "active" : "",
    onClick: () => onPanelChange("Skills")
  }, "Skills"), React.createElement("button", {
    className: activePanel === "Memory" ? "active" : "",
    onClick: () => onPanelChange("Memory")
  }, "Memory")));
}
function ChatWindow({
  user,
  messages,
  isLoading,
  error,
  onSend,
  onQuickStart,
  onStop
}) {
  const [draft, setDraft] = useState("");
  const hasMessages = messages.length > 0;
  const submit = () => {
    if (!draft.trim() || isLoading) return;
    onSend(draft);
    setDraft("");
  };
  return React.createElement("section", {
    className: `chat-window ${hasMessages ? "conversation-mode" : ""}`
  }, !hasMessages ? React.createElement(StartScreen, {
    user: user,
    draft: draft,
    setDraft: setDraft,
    submit: submit,
    onQuickStart: onQuickStart,
    isLoading: isLoading
  }) : React.createElement(React.Fragment, null, React.createElement(MessageList, {
    messages: messages
  }), React.createElement(Composer, {
    draft: draft,
    setDraft: setDraft,
    submit: submit,
    isLoading: isLoading,
    onStop: onStop,
    compact: true
  })), error ? React.createElement("div", {
    className: "inline-error"
  }, error) : null);
}
function StartScreen({
  user,
  draft,
  setDraft,
  submit,
  onQuickStart,
  isLoading
}) {
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "早上好" : hour < 18 ? "下午好" : "晚上好";
  return React.createElement("div", {
    className: "start-screen"
  }, React.createElement("div", {
    className: "mode-pill"
  }, "Agentic BI \xB7 Operations"), React.createElement("h1", null, greeting, "\uFF0C", user.userId), React.createElement("p", {
    className: "subtitle"
  }, "\u822A\u7A7A\u8FD0\u8425\u590D\u76D8"), React.createElement(Composer, {
    draft: draft,
    setDraft: setDraft,
    submit: submit,
    isLoading: isLoading
  }), React.createElement("div", {
    className: "quick-label"
  }, "Quick start"), React.createElement("div", {
    className: "quick-grid"
  }, quickStarts.map(item => React.createElement("button", {
    key: item.title,
    className: "quick-card",
    onClick: () => onQuickStart(item.body)
  }, React.createElement("span", {
    className: "quick-icon"
  }, item.icon), React.createElement("span", null, React.createElement("strong", null, item.title), React.createElement("small", null, item.body))))));
}
function Composer({
  draft,
  setDraft,
  submit,
  isLoading,
  onStop,
  compact = false
}) {
  const textRef = useRef(null);
  useEffect(() => {
    const el = textRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [draft]);
  return React.createElement("div", {
    className: `composer ${compact ? "compact" : ""}`
  }, React.createElement("textarea", {
    ref: textRef,
    value: draft,
    onChange: event => setDraft(event.target.value),
    onKeyDown: event => {
      if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
        event.preventDefault();
        submit();
      }
    },
    placeholder: "\u95EE\u4E00\u4E2A\u8FD0\u8425\u95EE\u9898\uFF0C\u4F8B\u5982\uFF1A\u6700\u8FD190\u5929\u54EA\u4E9B\u673A\u573A\u5EF6\u8BEF\u7387\u5F02\u5E38\uFF1F",
    rows: 1
  }), React.createElement("div", {
    className: "composer-actions"
  }, isLoading ? React.createElement("button", {
    className: "send-button",
    onClick: onStop,
    title: "\u505C\u6B62\u63A5\u6536",
    "aria-label": "\u505C\u6B62\u63A5\u6536"
  }, "\u25A0") : React.createElement("button", {
    className: "send-button",
    onClick: submit,
    disabled: !draft.trim(),
    title: "\u53D1\u9001",
    "aria-label": "\u53D1\u9001"
  }, "\u2191")));
}
function MessageList({
  messages
}) {
  const ref = useRef(null);
  const followRef = useRef(true);
  useEffect(() => {
    if (followRef.current) ref.current?.scrollTo({
      top: ref.current.scrollHeight
    });
    if (window.hljs) {
      setTimeout(() => {
        ref.current?.querySelectorAll("pre code").forEach(block => {
          try {
            window.hljs.highlightElement(block);
          } catch {}
        });
      }, 0);
    }
  }, [messages]);
  return React.createElement("div", {
    className: "message-list",
    ref: ref,
    onScroll: () => {
      const el = ref.current;
      followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
    }
  }, messages.map(message => React.createElement(MessageBubble, {
    key: message.id,
    message: message
  })));
}
function MessageBubble({
  message
}) {
  const isUser = message.role === "user";
  return React.createElement("article", {
    className: `message ${isUser ? "user" : "assistant"}`
  }, React.createElement("div", {
    className: "message-meta"
  }, isUser ? "You" : "Operations Agent"), React.createElement("div", {
    className: "message-body"
  }, message.status ? React.createElement("div", {
    className: "status-line"
  }, React.createElement("span", {
    className: "pulse"
  }), message.status) : null, message.error ? React.createElement("div", {
    className: "message-error"
  }, message.error) : null, message.text ? React.createElement(MarkdownBlock, {
    text: message.text
  }) : null, !isUser ? React.createElement("div", {
    className: "artifact-stack"
  }, React.createElement(AgentTraceTimeline, {
    traces: message.traces,
    plan: message.plan,
    intent: message.intent,
    quality: message.quality
  }), React.createElement(SQLPanel, {
    sql: message.sql,
    retryCount: message.retryCount,
    params: message.sqlParams
  }), React.createElement(DecisionPanel, {
    decision: message.decision
  }), React.createElement(ChartPanel, {
    chart: message.chart
  }), React.createElement(SourceList, {
    sources: message.sources
  })) : null));
}
function MarkdownBlock({
  text
}) {
  return React.createElement("div", {
    className: "markdown-body answer-body",
    dangerouslySetInnerHTML: {
      __html: renderMarkdown(text)
    }
  });
}
function AgentTraceTimeline({
  traces = [],
  plan,
  intent,
  quality
}) {
  if (!traces?.length && !plan && !intent && !quality) return null;
  const score = quality?.debate?.scorecard || {};
  const confidence = quality?.confidence_label;
  return React.createElement("details", {
    className: "artifact-panel"
  }, React.createElement("summary", null, "Agent trace"), React.createElement("div", {
    className: "trace-content"
  }, intent ? React.createElement("div", {
    className: "trace-chip"
  }, "Intent: ", intent) : null, confidence ? React.createElement("div", {
    className: "trace-chip"
  }, "Confidence: ", confidence) : null, score.total !== undefined ? React.createElement("div", {
    className: "trace-chip"
  }, "Score: ", score.total, "/", score.threshold) : null, quality?.critic ? React.createElement("div", {
    className: "trace-chip"
  }, "\u5BA1\u6821\uFF1A", quality.critic.status) : null, plan ? React.createElement("div", {
    className: "plan-box"
  }, React.createElement("strong", null, plan.goal || "任务计划"), React.createElement("ol", null, (plan.steps || []).map((step, index) => React.createElement("li", {
    key: index
  }, step.agent || "agent", " \xB7 ", step.task || "")))) : null, React.createElement("div", {
    className: "timeline"
  }, traces.map((trace, index) => React.createElement("div", {
    className: "timeline-row",
    key: `${trace.step}_${index}`
  }, React.createElement("span", null), React.createElement("div", null, React.createElement("strong", null, trace.step), React.createElement("small", null, trace.detail)))))));
}
function SQLPanel({
  sql,
  retryCount,
  params = []
}) {
  if (!sql) return null;
  return React.createElement("details", {
    className: "artifact-panel"
  }, React.createElement("summary", null, "SQL evidence ", retryCount ? `· repaired ${retryCount}x` : ""), React.createElement("pre", null, React.createElement("code", {
    className: "language-sql"
  }, sql)), params.length ? React.createElement("pre", null, "\u53C2\u6570\uFF1A", JSON.stringify(params)) : null);
}
function DecisionPanel({
  decision
}) {
  if (!decision) return null;
  return React.createElement("details", {
    className: "artifact-panel"
  }, React.createElement("summary", null, "\u884C\u52A8\u4F9D\u636E\u4E0E\u60C5\u666F\u5047\u8BBE"), React.createElement("div", {
    className: "decision-content"
  }, React.createElement("p", null, "\u539F\u56E0\u5B8C\u6574\u8986\u76D6\u7387\uFF1A", decision.diagnosis.component_coverage == null ? '未知' : `${(decision.diagnosis.component_coverage * 100).toFixed(1)}%`), decision.actions.recommendations.map((r, i) => React.createElement("div", {
    className: "decision-row",
    key: i
  }, React.createElement("strong", null, r.playbook.label), React.createElement("p", null, r.evidence), React.createElement("p", null, r.validation))), React.createElement("p", null, decision.actions.impact_estimate.assumption)));
}
function ChartPanel({
  chart
}) {
  const ref = useRef(null);
  useEffect(() => {
    if (!chart || !ref.current || !window.echarts) return;
    const instance = window.echarts.init(ref.current, null, {
      renderer: "canvas"
    });
    instance.setOption({
      backgroundColor: "transparent",
      grid: {
        left: "4%",
        right: "4%",
        bottom: "10%",
        containLabel: true
      },
      tooltip: {
        trigger: "axis"
      },
      ...chart
    });
    const resize = () => instance.resize();
    const observer = new ResizeObserver(resize);
    observer.observe(ref.current);
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      observer.disconnect();
      instance.dispose();
    };
  }, [chart]);
  if (!chart) return null;
  return React.createElement("div", {
    className: "chart-panel",
    ref: ref
  });
}
function SourceList({
  sources = []
}) {
  const valid = (sources || []).filter(url => {
    try {
      return ['http:', 'https:'].includes(new URL(url).protocol);
    } catch {
      return false;
    }
  }).slice(0, 5);
  if (!valid.length) return null;
  return React.createElement("div", {
    className: "source-list"
  }, React.createElement("div", {
    className: "source-title"
  }, "Sources"), valid.map((url, index) => {
    let label = url;
    try {
      label = new URL(url).hostname;
    } catch {}
    return React.createElement("a", {
      key: url + index,
      href: url,
      target: "_blank",
      rel: "noreferrer"
    }, index + 1, ". ", label);
  }));
}
function MemoryPanel({
  userInfo,
  onRefresh,
  onReset
}) {
  const preferences = userInfo?.preferences || {};
  const knowledge = Array.isArray(userInfo?.knowledge) ? userInfo.knowledge : [];
  return React.createElement("section", {
    className: "panel-page"
  }, React.createElement("div", {
    className: "panel-header"
  }, React.createElement("div", null, React.createElement("p", {
    className: "eyebrow"
  }, "Memory"), React.createElement("h1", null, "\u957F\u671F\u8BB0\u5FC6"), React.createElement("p", null, "\u5F53\u524D\u7528\u6237\u7684\u504F\u597D\u4E0E\u5DF2\u4FDD\u5B58\u4E0A\u4E0B\u6587\u3002")), React.createElement("div", {
    className: "panel-actions"
  }, React.createElement("button", {
    onClick: onRefresh
  }, "\u5237\u65B0"), React.createElement("button", {
    className: "danger",
    onClick: onReset
  }, "\u91CD\u7F6E"))), React.createElement("div", {
    className: "memory-grid"
  }, React.createElement("div", {
    className: "data-panel"
  }, React.createElement("h2", null, "\u7528\u6237\u753B\u50CF"), React.createElement("div", {
    className: "metric-row"
  }, React.createElement("span", null, "\u7528\u6237 ID"), React.createElement("strong", null, userInfo?.user_id || "-")), React.createElement("div", {
    className: "metric-row"
  }, React.createElement("span", null, "\u4F1A\u8BDD ID"), React.createElement("strong", null, userInfo?.session_id?.slice(0, 8) || "-")), React.createElement("div", {
    className: "metric-row"
  }, React.createElement("span", null, "\u504F\u597D\u6570\u91CF"), React.createElement("strong", null, Object.keys(preferences).length)), React.createElement("div", {
    className: "metric-row"
  }, React.createElement("span", null, "\u77E5\u8BC6\u6570\u91CF"), React.createElement("strong", null, knowledge.length))), React.createElement("div", {
    className: "data-panel"
  }, React.createElement("h2", null, "\u504F\u597D"), Object.keys(preferences).length ? Object.entries(preferences).map(([key, value]) => React.createElement("div", {
    className: "list-line",
    key: key
  }, React.createElement("strong", null, key), React.createElement("span", null, value))) : React.createElement("p", {
    className: "empty-text"
  }, "\u6682\u65E0\u504F\u597D\u8BB0\u5F55\u3002")), React.createElement("div", {
    className: "data-panel wide"
  }, React.createElement("h2", null, "\u77E5\u8BC6"), knowledge.length ? knowledge.slice(0, 12).map((item, index) => React.createElement("div", {
    className: "knowledge-item",
    key: index
  }, React.createElement("strong", null, item.category || "知识"), React.createElement("p", null, item.content || ""))) : React.createElement("p", {
    className: "empty-text"
  }, "\u6682\u65E0\u77E5\u8BC6\u8BB0\u5F55\u3002"))));
}
function SkillPanel({
  skills
}) {
  return React.createElement("section", {
    className: "panel-page"
  }, React.createElement("div", {
    className: "panel-header"
  }, React.createElement("div", null, React.createElement("p", {
    className: "eyebrow"
  }, "Registry"), React.createElement("h1", null, "Skill Registry"), React.createElement("p", null, "\u53EF\u7528\u5206\u6790\u80FD\u529B\u4E0E\u6743\u9650\u8303\u56F4\u3002"))), React.createElement("div", {
    className: "skill-grid"
  }, skills.map(skill => React.createElement("article", {
    className: "skill-card",
    key: skill.name
  }, React.createElement("div", {
    className: "skill-top"
  }, React.createElement("span", null, skill.type), React.createElement("strong", null, skill.name)), React.createElement("p", null, skill.description), React.createElement("div", {
    className: "permission-list"
  }, (skill.permissions || []).map(permission => React.createElement("code", {
    key: permission
  }, permission)))))));
}
ReactDOM.createRoot(document.getElementById("root")).render(React.createElement(App, null));
