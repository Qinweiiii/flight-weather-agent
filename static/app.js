const { useEffect, useRef, useState } = React;

const API_BASE_URL = window.location.origin;

const quickStarts = [
  {
    title: "机场延误诊断",
    body: "最近90天到达延误率最高的10个出发机场是哪些？",
    icon: "A",
  },
  {
    title: "航司取消率",
    body: "最近90天取消率最高的5家航司是哪些？请给出航班量和取消率。",
    icon: "C",
  },
  {
    title: "天气影响归因",
    body: "比较降水量高于2mm和低于等于2mm时的平均到达延误分钟数。",
    icon: "W",
  },
  {
    title: "行业基准对比",
    body: "结合行业基准，对比我们近90天到达延误率并给出优化建议。",
    icon: "B",
  },
];

const navItems = [
  ["Workbench", "工作台"],
  ["Memory", "记忆"],
  ["Trace", "追踪"],
  ["Skills", "技能"],
];

function apiCall(endpoint, data = {}) {
  return fetch(`${API_BASE_URL}/api/${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  }).then(async (response) => {
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
        headerIds: false,
      });
      return window.DOMPurify ? window.DOMPurify.sanitize(raw) : raw;
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

function useSSEChat({ userId, onMemoryRefresh }) {
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");

  const sendQuestion = async (question) => {
    const trimmed = question.trim();
    if (!trimmed || isLoading) return;

    setError("");
    setIsLoading(true);

    const userMessage = {
      id: uniqueId("user"),
      role: "user",
      text: trimmed,
      createdAt: new Date(),
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
      createdAt: new Date(),
    };

    setMessages((prev) => [...prev, userMessage, assistantMessage]);

    const patchAssistant = (patch) => {
      setMessages((prev) =>
        prev.map((message) => {
          if (message.id !== assistantId) return message;
          const nextPatch = typeof patch === "function" ? patch(message) : patch;
          return { ...message, ...nextPatch };
        })
      );
    };

    try {
      const response = await fetch(`${API_BASE_URL}/api/query_stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, question: trimmed }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const reader = response.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let fullAnswer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop();

        for (const part of parts) {
          const line = part.trim();
          if (!line.startsWith("data: ")) continue;
          const event = JSON.parse(line.slice(6));

          if (event.type === "status") patchAssistant({ status: event.message || "处理中..." });
          if (event.type === "intent") patchAssistant({ intent: event.intent || "" });
          if (event.type === "plan") patchAssistant({ plan: event.plan || null });
          if (event.type === "trace") {
            patchAssistant((message) => ({
              traces: [...(message.traces || []), {
                step: event.step || "trace",
                detail: event.detail || "",
              }],
            }));
          }
          if (event.type === "quality") patchAssistant({ quality: event.quality || null });
          if (event.type === "sql") {
            patchAssistant({
              sql: event.sql || "",
              retryCount: event.retry_count || 0,
            });
          }
          if (event.type === "sources") patchAssistant({ sources: event.sources || [] });
          if (event.type === "chart") patchAssistant({ chart: event.config || null });
          if (event.type === "chunk") {
            fullAnswer += event.content || "";
            patchAssistant({ text: fullAnswer, status: "" });
          }
          if (event.type === "error") {
            patchAssistant({
              error: event.message || "处理过程中出现错误",
              status: event.message || "处理过程中出现错误",
            });
          }
          if (event.type === "done") {
            if (event.answer && !fullAnswer) {
              fullAnswer = event.answer;
              patchAssistant({ text: fullAnswer });
            }
            patchAssistant({ status: "" });
          }
        }
      }

      if (onMemoryRefresh) onMemoryRefresh();
    } catch (err) {
      const message = err.message || "请求失败";
      setError(message);
      patchAssistant({
        text: `抱歉，发生错误：${message}`,
        error: message,
        status: "",
      });
    } finally {
      setIsLoading(false);
    }
  };

  const resetMessages = (note = "") => {
    setMessages(note ? [{
      id: uniqueId("system"),
      role: "assistant",
      text: note,
      createdAt: new Date(),
    }] : []);
  };

  return { messages, isLoading, error, sendQuestion, resetMessages };
}

function App() {
  const [user, setUser] = useState(null);
  const [userInfo, setUserInfo] = useState(null);
  const [activePanel, setActivePanel] = useState("Workbench");
  const [skills, setSkills] = useState([]);
  const [toast, setToast] = useState("");

  const refreshUserInfo = async () => {
    if (!user?.userId) return;
    try {
      const result = await apiCall("user_info", { user_id: user.userId });
      if (result.success) setUserInfo(result.user_info);
    } catch (error) {
      console.warn("Failed to refresh user info:", error);
    }
  };

  const { messages, isLoading, error, sendQuestion, resetMessages } = useSSEChat({
    userId: user?.userId,
    onMemoryRefresh: refreshUserInfo,
  });

  useEffect(() => {
    fetch(`${API_BASE_URL}/api/skills`)
      .then((res) => res.json())
      .then((payload) => setSkills(payload?.registry?.skills || []))
      .catch(() => setSkills([]));
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 3200);
    return () => clearTimeout(timer);
  }, [toast]);

  const handleLogin = async (userId) => {
    const result = await apiCall("login", { user_id: userId || "guest" });
    if (result.success) {
      setUser({
        userId: result.user_id,
        sessionId: result.session_id,
      });
      setUserInfo(result.user_info || null);
      resetMessages("");
    }
  };

  const handleNewSession = async () => {
    if (!user) return;
    const result = await apiCall("new_session", { user_id: user.userId });
    if (result.success) {
      setUser((prev) => ({ ...prev, sessionId: result.session_id }));
      resetMessages("新会话已开始。");
      setToast("新会话已创建");
    }
  };

  const handleResetMemory = async () => {
    if (!window.confirm("确定要重置长期记忆库吗？此操作不可恢复。")) return;
    const result = await apiCall("reset_memory", { confirm: true });
    if (result.success) {
      setUserInfo(null);
      resetMessages("长期记忆库已重置。");
      setToast("记忆库已重置");
    }
  };

  if (!user) return <LoginPanel onLogin={handleLogin} />;

  return (
    <div className="app-shell">
      <Sidebar
        user={user}
        userInfo={userInfo}
        activePanel={activePanel}
        onPanelChange={setActivePanel}
        onNewSession={handleNewSession}
        onResetMemory={handleResetMemory}
        onLogout={() => window.location.reload()}
        skills={skills}
      />
      <main className="workspace">
        <TopBar activePanel={activePanel} onPanelChange={setActivePanel} />
        {activePanel === "Memory" ? (
          <MemoryPanel userInfo={userInfo} onRefresh={refreshUserInfo} onReset={handleResetMemory} />
        ) : activePanel === "Skills" ? (
          <SkillPanel skills={skills} />
        ) : (
          <ChatWindow
            user={user}
            messages={messages}
            isLoading={isLoading}
            error={error}
            onSend={sendQuestion}
            onQuickStart={sendQuestion}
          />
        )}
      </main>
      {toast ? <div className="toast">{toast}</div> : null}
    </div>
  );
}

function LoginPanel({ onLogin }) {
  const [userId, setUserId] = useState("guest");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event) => {
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

  return (
    <div className="login-stage">
      <section className="login-panel">
        <div className="window-dots" aria-hidden="true">
          <span></span><span></span><span></span>
        </div>
        <p className="eyebrow">Flight Operations Agentic BI</p>
        <h1>航空运营诊断工作台</h1>
        <p className="login-copy">
          用历史航班和天气数据做运营复盘、异常发现、天气影响归因与行业基准对比。
        </p>
        <form onSubmit={submit} className="login-form">
          <label htmlFor="userId">用户 ID</label>
          <div className="inline-input">
            <input
              id="userId"
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              placeholder="guest"
            />
            <button type="submit" disabled={loading}>{loading ? "进入中" : "进入"}</button>
          </div>
          {error ? <div className="form-error">{error}</div> : null}
        </form>
      </section>
    </div>
  );
}

function Sidebar({
  user,
  userInfo,
  activePanel,
  onPanelChange,
  onNewSession,
  onResetMemory,
  onLogout,
  skills,
}) {
  const preferenceCount = userInfo?.preferences ? Object.keys(userInfo.preferences).length : 0;
  const knowledgeCount = Array.isArray(userInfo?.knowledge) ? userInfo.knowledge.length : 0;

  return (
    <aside className="sidebar">
      <div className="window-dots" aria-hidden="true">
        <span></span><span></span><span></span>
      </div>
      <button className="nav-command" onClick={onNewSession}><span>+</span> 新会话</button>
      <nav className="nav-list">
        {navItems.map(([key, label]) => (
          <button
            key={key}
            className={activePanel === key ? "active" : ""}
            onClick={() => onPanelChange(key)}
          >
            <span className="nav-icon">{key.slice(0, 1)}</span>
            {label}
          </button>
        ))}
      </nav>

      <div className="sidebar-section">
        <div className="section-title">Recents</div>
        <div className="recent-item">
          <strong>延误率异常诊断</strong>
          <span>机场、航司、天气因素联合分析</span>
        </div>
        <div className="recent-item">
          <strong>行业基准对比</strong>
          <span>内部 SQL 证据与外部资料裁决</span>
        </div>
        <div className="recent-item">
          <strong>运营周报摘要</strong>
          <span>航班量、取消率、改降率复盘</span>
        </div>
      </div>

      <div className="sidebar-section compact">
        <div className="section-title">System</div>
        <div className="metric-row"><span>Skills</span><strong>{skills.length}</strong></div>
        <div className="metric-row"><span>Preferences</span><strong>{preferenceCount}</strong></div>
        <div className="metric-row"><span>Knowledge</span><strong>{knowledgeCount}</strong></div>
        <button className="quiet-action" onClick={onResetMemory}>重置记忆</button>
      </div>

      <div className="sidebar-user">
        <div className="avatar">{(user.userId || "G").slice(0, 1).toUpperCase()}</div>
        <div>
          <strong>{user.userId}</strong>
          <span>{user.sessionId ? `${user.sessionId.slice(0, 8)}...` : "-"}</span>
        </div>
        <button className="icon-button" onClick={onLogout} title="退出">↗</button>
      </div>
    </aside>
  );
}

function TopBar({ activePanel, onPanelChange }) {
  return (
    <header className="topbar">
      <div className="segmented" aria-label="workspace sections">
        <button className={activePanel === "Workbench" ? "active" : ""} onClick={() => onPanelChange("Workbench")}>Workbench</button>
        <button className={activePanel === "Trace" ? "active" : ""} onClick={() => onPanelChange("Trace")}>Trace</button>
        <button className={activePanel === "Skills" ? "active" : ""} onClick={() => onPanelChange("Skills")}>Skills</button>
      </div>
    </header>
  );
}

function ChatWindow({ user, messages, isLoading, error, onSend, onQuickStart }) {
  const [draft, setDraft] = useState("");
  const hasMessages = messages.length > 0;

  const submit = () => {
    if (!draft.trim()) return;
    onSend(draft);
    setDraft("");
  };

  return (
    <section className={`chat-window ${hasMessages ? "conversation-mode" : ""}`}>
      {!hasMessages ? (
        <StartScreen user={user} draft={draft} setDraft={setDraft} submit={submit} onQuickStart={onQuickStart} isLoading={isLoading} />
      ) : (
        <>
          <MessageList messages={messages} />
          <Composer draft={draft} setDraft={setDraft} submit={submit} isLoading={isLoading} compact />
        </>
      )}
      {error ? <div className="inline-error">{error}</div> : null}
    </section>
  );
}

function StartScreen({ user, draft, setDraft, submit, onQuickStart, isLoading }) {
  const hour = new Date().getHours();
  const greeting = hour < 12 ? "早上好" : hour < 18 ? "下午好" : "晚上好";

  return (
    <div className="start-screen">
      <div className="mode-pill">Agentic BI · Operations</div>
      <h1>{greeting}，{user.userId}</h1>
      <p className="subtitle">面向航空运营团队的历史数据复盘、异常诊断和基准对比工作台。</p>
      <Composer draft={draft} setDraft={setDraft} submit={submit} isLoading={isLoading} />
      <div className="quick-label">Quick start</div>
      <div className="quick-grid">
        {quickStarts.map((item) => (
          <button key={item.title} className="quick-card" onClick={() => onQuickStart(item.body)}>
            <span className="quick-icon">{item.icon}</span>
            <span>
              <strong>{item.title}</strong>
              <small>{item.body}</small>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

function Composer({ draft, setDraft, submit, isLoading, compact = false }) {
  const textRef = useRef(null);

  useEffect(() => {
    const el = textRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [draft]);

  return (
    <div className={`composer ${compact ? "compact" : ""}`}>
      <textarea
        ref={textRef}
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit();
          }
        }}
        placeholder="问一个运营问题，例如：最近90天哪些机场延误率异常？"
        rows={1}
      />
      <div className="composer-actions">
        <button className="tool-button" title="SQL evidence">SQL</button>
        <button className="tool-button" title="Benchmark search">WEB</button>
        <button className="send-button" onClick={submit} disabled={isLoading || !draft.trim()} title="发送">↑</button>
      </div>
    </div>
  );
}

function MessageList({ messages }) {
  const ref = useRef(null);

  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight, behavior: "smooth" });
    if (window.hljs) {
      setTimeout(() => {
        ref.current?.querySelectorAll("pre code").forEach((block) => {
          try { window.hljs.highlightElement(block); } catch {}
        });
      }, 0);
    }
  }, [messages]);

  return (
    <div className="message-list" ref={ref}>
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} />
      ))}
    </div>
  );
}

function MessageBubble({ message }) {
  const isUser = message.role === "user";
  return (
    <article className={`message ${isUser ? "user" : "assistant"}`}>
      <div className="message-meta">{isUser ? "You" : "Operations Agent"}</div>
      <div className="message-body">
        {message.status ? <div className="status-line"><span className="pulse"></span>{message.status}</div> : null}
        {message.error ? <div className="message-error">{message.error}</div> : null}
        {message.text ? <MarkdownBlock text={message.text} /> : null}
        {!isUser ? (
          <div className="artifact-stack">
            <AgentTraceTimeline traces={message.traces} plan={message.plan} intent={message.intent} quality={message.quality} />
            <SQLPanel sql={message.sql} retryCount={message.retryCount} />
            <ChartPanel chart={message.chart} />
            <SourceList sources={message.sources} />
          </div>
        ) : null}
      </div>
    </article>
  );
}

function MarkdownBlock({ text }) {
  return (
    <div
      className="markdown-body answer-body"
      dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }}
    />
  );
}

function AgentTraceTimeline({ traces = [], plan, intent, quality }) {
  if (!traces?.length && !plan && !intent && !quality) return null;
  const score = quality?.debate?.scorecard || {};
  const confidence = quality?.confidence_label;

  return (
    <details className="artifact-panel">
      <summary>Agent trace</summary>
      <div className="trace-content">
        {intent ? <div className="trace-chip">Intent: {intent}</div> : null}
        {confidence ? <div className="trace-chip">Confidence: {confidence}</div> : null}
        {score.total !== undefined ? <div className="trace-chip">Score: {score.total}/{score.threshold}</div> : null}
        {plan ? (
          <div className="plan-box">
            <strong>{plan.goal || "任务计划"}</strong>
            <ol>
              {(plan.steps || []).map((step, index) => (
                <li key={index}>{step.agent || "agent"} · {step.task || ""}</li>
              ))}
            </ol>
          </div>
        ) : null}
        <div className="timeline">
          {traces.map((trace, index) => (
            <div className="timeline-row" key={`${trace.step}_${index}`}>
              <span></span>
              <div><strong>{trace.step}</strong><small>{trace.detail}</small></div>
            </div>
          ))}
        </div>
      </div>
    </details>
  );
}

function SQLPanel({ sql, retryCount }) {
  if (!sql) return null;
  return (
    <details className="artifact-panel">
      <summary>SQL evidence {retryCount ? `· repaired ${retryCount}x` : ""}</summary>
      <pre><code className="language-sql">{sql}</code></pre>
    </details>
  );
}

function ChartPanel({ chart }) {
  const ref = useRef(null);

  useEffect(() => {
    if (!chart || !ref.current || !window.echarts) return;
    const instance = window.echarts.init(ref.current, null, { renderer: "canvas" });
    instance.setOption({
      backgroundColor: "transparent",
      grid: { left: "4%", right: "4%", bottom: "10%", containLabel: true },
      tooltip: { trigger: "axis" },
      ...chart,
    });
    const resize = () => instance.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      instance.dispose();
    };
  }, [chart]);

  if (!chart) return null;
  return <div className="chart-panel" ref={ref}></div>;
}

function SourceList({ sources = [] }) {
  const valid = (sources || []).filter(Boolean).slice(0, 5);
  if (!valid.length) return null;
  return (
    <div className="source-list">
      <div className="source-title">Sources</div>
      {valid.map((url, index) => {
        let label = url;
        try { label = new URL(url).hostname; } catch {}
        return <a key={url + index} href={url} target="_blank" rel="noreferrer">{index + 1}. {label}</a>;
      })}
    </div>
  );
}

function MemoryPanel({ userInfo, onRefresh, onReset }) {
  const preferences = userInfo?.preferences || {};
  const knowledge = Array.isArray(userInfo?.knowledge) ? userInfo.knowledge : [];

  return (
    <section className="panel-page">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Memory</p>
          <h1>长期记忆</h1>
          <p>展示用户偏好和知识沉淀，便于说明 Agent 的上下文管理能力。</p>
        </div>
        <div className="panel-actions">
          <button onClick={onRefresh}>刷新</button>
          <button className="danger" onClick={onReset}>重置</button>
        </div>
      </div>
      <div className="memory-grid">
        <div className="data-panel">
          <h2>用户画像</h2>
          <div className="metric-row"><span>用户 ID</span><strong>{userInfo?.user_id || "-"}</strong></div>
          <div className="metric-row"><span>会话 ID</span><strong>{userInfo?.session_id?.slice(0, 8) || "-"}</strong></div>
          <div className="metric-row"><span>偏好数量</span><strong>{Object.keys(preferences).length}</strong></div>
          <div className="metric-row"><span>知识数量</span><strong>{knowledge.length}</strong></div>
        </div>
        <div className="data-panel">
          <h2>偏好</h2>
          {Object.keys(preferences).length ? Object.entries(preferences).map(([key, value]) => (
            <div className="list-line" key={key}><strong>{key}</strong><span>{value}</span></div>
          )) : <p className="empty-text">暂无偏好记录。</p>}
        </div>
        <div className="data-panel wide">
          <h2>知识</h2>
          {knowledge.length ? knowledge.slice(0, 12).map((item, index) => (
            <div className="knowledge-item" key={index}>
              <strong>{item.category || "知识"}</strong>
              <p>{item.content || ""}</p>
            </div>
          )) : <p className="empty-text">暂无知识记录。</p>}
        </div>
      </div>
    </section>
  );
}

function SkillPanel({ skills }) {
  return (
    <section className="panel-page">
      <div className="panel-header">
        <div>
          <p className="eyebrow">Registry</p>
          <h1>Skill Registry</h1>
          <p>用轻量声明式配置展示工具边界、输入输出和权限要求。</p>
        </div>
      </div>
      <div className="skill-grid">
        {skills.map((skill) => (
          <article className="skill-card" key={skill.name}>
            <div className="skill-top">
              <span>{skill.type}</span>
              <strong>{skill.name}</strong>
            </div>
            <p>{skill.description}</p>
            <div className="permission-list">
              {(skill.permissions || []).map((permission) => <code key={permission}>{permission}</code>)}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
