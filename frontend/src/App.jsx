import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

const starterPrompts = [
  "My washing machine won't drain and shows error E21",
  "What warranty does the WM-FL500 washer have?",
  "Summarize the uploaded policy document for me",
];

const authModes = [
  { key: "login", label: "User Login" },
  { key: "register", label: "Register" },
  { key: "admin", label: "Admin Login" },
];

function classNames(...values) {
  return values.filter(Boolean).join(" ");
}

function buildAuthHeaders(token) {
  return { "Content-Type": "application/json", Authorization: `Bearer ${token}` };
}

async function readResponsePayload(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  const text = await response.text();
  return {
    error:
      response.status === 413
        ? "Uploaded file is too large. Current upload limit is 50 MB."
        : text || `Request failed with status ${response.status}`,
  };
}

function TypingIndicator() {
  return (
    <article className="message-card assistant typing-indicator-card">
      <p className="message-role">FixMate AI</p>
      <span className="typing-indicator">
        <span /><span /><span />
      </span>
    </article>
  );
}

export default function App() {
  const [token, setToken] = useState(() => localStorage.getItem("fixmate_token") || "");
  const [currentUser, setCurrentUser] = useState(null);
  const [authMode, setAuthMode] = useState("login");
  const [authForm, setAuthForm] = useState({ name: "", identifier: "", password: "" });
  const [authError, setAuthError] = useState("");
  const [authLoading, setAuthLoading] = useState(false);

  const [threads, setThreads] = useState([]);
  const [activeThreadId, setActiveThreadId] = useState(null);
  const [threadDetails, setThreadDetails] = useState({ summaries: [], messages: [], thread: null });
  const [draft, setDraft] = useState("");
  const [chatLoading, setChatLoading] = useState(false);

  const [adminFiles, setAdminFiles] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [adminMessage, setAdminMessage] = useState("");
  const [adminMessageType, setAdminMessageType] = useState("success");

  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);

  const isAdmin = currentUser?.role === "admin";
  const isUser = currentUser?.role === "user";

  useEffect(() => {
    if (!token) { setCurrentUser(null); return; }
    let cancelled = false;
    async function loadMe() {
      try {
        const response = await fetch("/api/auth/me", { headers: { Authorization: `Bearer ${token}` } });
        if (!response.ok) throw new Error("Session expired");
        const payload = await readResponsePayload(response);
        if (!cancelled) setCurrentUser(payload.user);
      } catch {
        if (!cancelled) { localStorage.removeItem("fixmate_token"); setToken(""); setCurrentUser(null); }
      }
    }
    void loadMe();
    return () => { cancelled = true; };
  }, [token]);

  useEffect(() => { if (isUser) void loadThreads(); }, [isUser]);
  useEffect(() => { if (isAdmin) void loadAdminFiles(); }, [isAdmin]);
  useEffect(() => { if (isUser && activeThreadId) void loadThread(activeThreadId); }, [isUser, activeThreadId]);

  // Auto-scroll to bottom when messages change
  useLayoutEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [threadDetails.messages, chatLoading]);

  // Auto-resize textarea
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }, [draft]);

  const canSend = useMemo(() => draft.trim().length > 0 && !chatLoading, [draft, chatLoading]);

  async function loadThreads() {
    const response = await fetch("/api/chat-threads", { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) return;
    const payload = await readResponsePayload(response);
    setThreads(payload.threads);
    if (!activeThreadId && payload.threads.length > 0) setActiveThreadId(payload.threads[0].id);
  }

  async function createThread() {
    const response = await fetch("/api/chat-threads", {
      method: "POST",
      headers: buildAuthHeaders(token),
      body: JSON.stringify({ title: "New chat" }),
    });
    if (!response.ok) return;
    const payload = await readResponsePayload(response);
    await loadThreads();
    setActiveThreadId(payload.thread.id);
    setThreadDetails({ summaries: [], messages: [], thread: payload.thread });
  }

  async function loadThread(threadId) {
    const response = await fetch(`/api/chat-threads/${threadId}`, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) return;
    const payload = await readResponsePayload(response);
    setThreadDetails(payload);
  }

  async function sendMessage(text) {
    const trimmed = text.trim();
    if (!trimmed || chatLoading) return;

    setChatLoading(true);
    setDraft("");

    let threadId = activeThreadId;
    if (!threadId) {
      const response = await fetch("/api/chat-threads", {
        method: "POST",
        headers: buildAuthHeaders(token),
        body: JSON.stringify({ title: trimmed.slice(0, 60) }),
      });
      if (!response.ok) { setChatLoading(false); return; }
      const payload = await readResponsePayload(response);
      threadId = payload.thread.id;
      setActiveThreadId(threadId);
    }

    setThreadDetails((current) => ({
      ...current,
      messages: [...(current.messages || []), { id: crypto.randomUUID(), role: "user", content: trimmed }],
    }));

    try {
      const response = await fetch(`/api/chat-threads/${threadId}/messages`, {
        method: "POST",
        headers: buildAuthHeaders(token),
        body: JSON.stringify({ message: trimmed }),
      });
      const payload = await readResponsePayload(response);
      if (!response.ok) throw new Error(payload.error || "FixMate AI could not save that message.");
      await loadThreads();
      await loadThread(threadId);
    } catch (error) {
      setThreadDetails((current) => ({
        ...current,
        messages: [
          ...(current.messages || []),
          { id: crypto.randomUUID(), role: "assistant", content: error.message || "Something went wrong.", tone: "error" },
        ],
      }));
    } finally {
      setChatLoading(false);
    }
  }

  async function loadAdminFiles() {
    const response = await fetch("/api/admin/files", { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) return;
    const payload = await readResponsePayload(response);
    setAdminFiles(payload.files);
  }

  function setStatus(message, type = "success") {
    setAdminMessage(message);
    setAdminMessageType(type);
  }

  async function handleAuthSubmit(event) {
    event.preventDefault();
    setAuthLoading(true);
    setAuthError("");
    const endpoint = authMode === "register" ? "/api/auth/register" : "/api/auth/login";
    const body =
      authMode === "register"
        ? { name: authForm.name, email: authForm.identifier, password: authForm.password }
        : { role: authMode === "admin" ? "admin" : "user", identifier: authForm.identifier, password: authForm.password };
    try {
      const response = await fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const payload = await readResponsePayload(response);
      if (!response.ok) throw new Error(payload.error || "Authentication failed");
      localStorage.setItem("fixmate_token", payload.token);
      setToken(payload.token);
      setCurrentUser(payload.user);
      setAuthForm({ name: "", identifier: "", password: "" });
    } catch (error) {
      setAuthError(error.message || "Authentication failed");
    } finally {
      setAuthLoading(false);
    }
  }

  async function handleUpload(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setAdminMessage("");
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await fetch("/api/admin/files", { method: "POST", headers: { Authorization: `Bearer ${token}` }, body: formData });
      const payload = await readResponsePayload(response);
      if (!response.ok) throw new Error(payload.error || "Upload failed");
      setStatus(`✓ Indexed "${payload.file.original_name}"`);
      await loadAdminFiles();
    } catch (error) {
      setStatus(error.message || "Upload failed", "error");
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }

  async function deleteFile(fileId, fileName) {
    if (!confirm(`Delete "${fileName}"?`)) return;
    const response = await fetch(`/api/admin/files/${fileId}`, { method: "DELETE", headers: { Authorization: `Bearer ${token}` } });
    const payload = await readResponsePayload(response);
    if (!response.ok) { setStatus(payload.error || "Delete failed", "error"); return; }
    setStatus(`✓ Deleted "${payload.deleted.original_name}"`);
    await loadAdminFiles();
  }

  async function reindexFiles() {
    setAdminMessage("Reindexing…");
    setAdminMessageType("info");
    const response = await fetch("/api/admin/reindex", { method: "POST", headers: { Authorization: `Bearer ${token}` } });
    const payload = await readResponsePayload(response);
    if (!response.ok) { setStatus(payload.error || "Reindex failed", "error"); return; }
    setStatus(`✓ Reindexed ${payload.documents_indexed} documents`);
  }

  function logout() {
    localStorage.removeItem("fixmate_token");
    setToken("");
    setCurrentUser(null);
    setThreads([]);
    setActiveThreadId(null);
    setThreadDetails({ summaries: [], messages: [], thread: null });
    setAdminFiles([]);
  }

  function handleComposerKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (canSend) void sendMessage(draft);
    }
  }

  // ── Auth screen ──────────────────────────────────────────────────────────────
  if (!currentUser) {
    return (
      <div className="auth-shell">
        <div className="hero-glow hero-glow-left" />
        <div className="hero-glow hero-glow-right" />
        <main className="auth-layout">
          <section className="auth-brand">
            <p className="eyebrow">Support Intelligence Platform</p>
            <h1>FixMate AI</h1>
            <p className="brand-copy">
              Register as a user to chat with uploaded knowledge and appliance support workflows, or sign in as admin to manage indexed documents.
            </p>
          </section>

          <section className="auth-card">
            <div className="tab-row">
              {authModes.map((mode) => (
                <button
                  key={mode.key}
                  type="button"
                  className={classNames("tab-button", authMode === mode.key && "active")}
                  onClick={() => { setAuthMode(mode.key); setAuthError(""); }}
                >
                  {mode.label}
                </button>
              ))}
            </div>

            <form className="auth-form" onSubmit={handleAuthSubmit}>
              {authMode === "register" && (
                <label>
                  <span>Name</span>
                  <input
                    value={authForm.name}
                    onChange={(e) => setAuthForm((c) => ({ ...c, name: e.target.value }))}
                    placeholder="Your full name"
                    autoComplete="name"
                  />
                </label>
              )}

              <label>
                <span>{authMode === "admin" ? "Admin username" : "Email"}</span>
                <input
                  value={authForm.identifier}
                  onChange={(e) => setAuthForm((c) => ({ ...c, identifier: e.target.value }))}
                  placeholder={authMode === "admin" ? "admin" : "you@example.com"}
                  autoComplete={authMode === "admin" ? "username" : "email"}
                  type={authMode === "admin" ? "text" : "email"}
                />
              </label>

              <label>
                <span>Password</span>
                <input
                  type="password"
                  value={authForm.password}
                  onChange={(e) => setAuthForm((c) => ({ ...c, password: e.target.value }))}
                  placeholder="Password"
                  autoComplete={authMode === "register" ? "new-password" : "current-password"}
                />
              </label>

              {authError && <p className="form-error" role="alert">{authError}</p>}

              <button type="submit" className="primary-button" disabled={authLoading}>
                {authLoading
                  ? <span className="btn-spinner" />
                  : authMode === "register" ? "Create account" : "Sign in"}
              </button>
            </form>
          </section>
        </main>
      </div>
    );
  }

  // ── Admin screen ─────────────────────────────────────────────────────────────
  if (isAdmin) {
    return (
      <div className="dashboard-shell admin-shell">
        <aside className="sidebar-panel admin-panel">
          <div>
            <p className="eyebrow">Administrator</p>
            <h2>FixMate AI</h2>
            <p className="sidebar-copy">Upload support files, inspect the indexed library, and trigger reindexing when documents change.</p>
          </div>

          <label className={classNames("upload-card", uploading && "uploading")}>
            <span className="upload-icon">↑</span>
            <span>{uploading ? "Uploading…" : "Upload file"}</span>
            <span className="upload-hint">PDF, DOCX, TXT</span>
            <input type="file" onChange={handleUpload} disabled={uploading} accept=".pdf,.docx,.txt,.md,.csv" />
          </label>

          <button type="button" className="secondary-button" onClick={reindexFiles}>
            Reindex library
          </button>

          {adminMessage && (
            <p className={classNames("status-text", `status-${adminMessageType}`)} role="status">
              {adminMessage}
            </p>
          )}

          <button type="button" className="ghost-button logout-button" onClick={logout}>
            Log out
          </button>
        </aside>

        <main className="content-panel admin-content">
          <header className="panel-header">
            <div>
              <p className="chat-title">Knowledge Files</p>
              <p className="chat-subtitle">
                {adminFiles.length > 0
                  ? `${adminFiles.length} file${adminFiles.length !== 1 ? "s" : ""} indexed — searchable by all users`
                  : "Upload files to make them searchable for users"}
              </p>
            </div>
          </header>

          <div className="file-grid">
            {adminFiles.map((file) => (
              <article key={file.id} className="file-card">
                <div className="file-icon">📄</div>
                <div className="file-info">
                  <p className="file-name">{file.original_name}</p>
                  <p className="file-meta">{Math.max(1, Math.round(file.size_bytes / 1024))} KB · Uploaded by {file.uploaded_by}</p>
                </div>
                <button
                  type="button"
                  className="danger-button"
                  onClick={() => void deleteFile(file.id, file.original_name)}
                  aria-label={`Delete ${file.original_name}`}
                >
                  Delete
                </button>
              </article>
            ))}
            {adminFiles.length === 0 && (
              <div className="empty-state-card">
                <span className="empty-icon">📂</span>
                <p>No files uploaded yet.</p>
                <p className="empty-hint">Use the upload button to add your first document.</p>
              </div>
            )}
          </div>
        </main>
      </div>
    );
  }

  // ── User chat screen ──────────────────────────────────────────────────────────
  return (
    <div className="dashboard-shell">
      <aside className="sidebar-panel">
        <div className="sidebar-header">
          <p className="eyebrow">Workspace</p>
          <h2>FixMate AI</h2>
        </div>

        <button type="button" className="primary-button new-chat-btn" onClick={() => void createThread()}>
          + New chat
        </button>

        <nav className="thread-list" aria-label="Chat history">
          {threads.map((thread) => (
            <button
              key={thread.id}
              type="button"
              className={classNames("thread-item", activeThreadId === thread.id && "active")}
              onClick={() => setActiveThreadId(thread.id)}
              title={thread.title}
            >
              <span className="thread-icon">💬</span>
              <span className="thread-title">{thread.title}</span>
            </button>
          ))}
          {threads.length === 0 && <p className="empty-state">No chats yet.</p>}
        </nav>

        <div className="sidebar-footer">
          <div className="user-badge">
            <span className="user-avatar">{currentUser.name?.[0]?.toUpperCase()}</span>
            <span className="user-name">{currentUser.name}</span>
          </div>
          <button type="button" className="ghost-button icon-button" onClick={logout} title="Log out" aria-label="Log out">
            ⎋
          </button>
        </div>
      </aside>

      <main className="content-panel">
        <header className="panel-header">
          <div>
            <p className="chat-title">
              {activeThreadId ? (threadDetails.thread?.title || "Chat") : `Hello, ${currentUser.name.split(" ")[0]} 👋`}
            </p>
            <p className="chat-subtitle">
              {activeThreadId ? "Ask about appliance support or uploaded documents" : "What can I help you with today?"}
            </p>
          </div>
          <span className={classNames("status-pill", chatLoading ? "busy" : "idle")}>
            {chatLoading ? "Thinking…" : "Online"}
          </span>
        </header>

        {!activeThreadId ? (
          <section className="welcome-card">
            <p className="prompt-heading">Try asking</p>
            <div className="prompt-list">
              {starterPrompts.map((prompt) => (
                <button key={prompt} type="button" className="prompt-chip" onClick={() => void sendMessage(prompt)}>
                  {prompt}
                </button>
              ))}
            </div>
          </section>
        ) : (
          <section className="chat-workspace">
            {threadDetails.summaries.length > 0 && (
              <div className="summary-strip">
                {threadDetails.summaries.map((summary) => (
                  <article key={summary.id} className="summary-card">
                    <p className="message-role">Earlier summary</p>
                    <p className="message-text">{summary.summary}</p>
                  </article>
                ))}
              </div>
            )}

            <div className="message-list workspace-messages" role="log" aria-live="polite">
              {threadDetails.messages.map((message) => (
                <article key={message.id} className={classNames("message-card", message.role, message.tone)}>
                  <p className="message-role">{message.role === "assistant" ? "FixMate AI" : "You"}</p>
                  <p className="message-text">{message.content}</p>
                </article>
              ))}
              {chatLoading && <TypingIndicator />}
              <div ref={messagesEndRef} />
            </div>

            <form
              className="composer"
              onSubmit={(e) => { e.preventDefault(); void sendMessage(draft); }}
            >
              <textarea
                ref={textareaRef}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={handleComposerKeyDown}
                placeholder="Ask a question… (Enter to send, Shift+Enter for new line)"
                rows={1}
                aria-label="Message input"
              />
              <button type="submit" className="send-button" disabled={!canSend} aria-label="Send message">
                ↑
              </button>
            </form>
          </section>
        )}
      </main>
    </div>
  );
}
