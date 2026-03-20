import { useEffect, useMemo, useState } from "react";

const starterPrompts = [
  "Summarize the uploaded policy document for me",
  "My washing machine won't drain and shows error E21",
  "What warranty does the WM-FL500 washer have?",
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
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}

async function readResponsePayload(response) {
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return response.json();
  }

  const text = await response.text();
  return {
    error: response.status === 413
      ? "Uploaded file is too large. Current upload limit is 50 MB."
      : text || `Request failed with status ${response.status}`,
  };
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

  const isAdmin = currentUser?.role === "admin";
  const isUser = currentUser?.role === "user";

  useEffect(() => {
    if (!token) {
      setCurrentUser(null);
      return;
    }

    let cancelled = false;
    async function loadMe() {
      try {
        const response = await fetch("/api/auth/me", {
          headers: {
            Authorization: `Bearer ${token}`,
          },
        });
        if (!response.ok) {
          throw new Error("Session expired");
        }
        const payload = await readResponsePayload(response);
        if (!cancelled) {
          setCurrentUser(payload.user);
        }
      } catch {
        if (!cancelled) {
          localStorage.removeItem("fixmate_token");
          setToken("");
          setCurrentUser(null);
        }
      }
    }

    void loadMe();
    return () => {
      cancelled = true;
    };
  }, [token]);

  useEffect(() => {
    if (!isUser) {
      return;
    }
    void loadThreads();
  }, [isUser]);

  useEffect(() => {
    if (!isAdmin) {
      return;
    }
    void loadAdminFiles();
  }, [isAdmin]);

  useEffect(() => {
    if (!isUser || !activeThreadId) {
      return;
    }
    void loadThread(activeThreadId);
  }, [isUser, activeThreadId]);

  const canSend = useMemo(() => draft.trim().length > 0 && !chatLoading, [draft, chatLoading]);

  async function loadThreads() {
    const response = await fetch("/api/chat-threads", {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) {
      return;
    }
    const payload = await readResponsePayload(response);
    setThreads(payload.threads);
    if (!activeThreadId && payload.threads.length > 0) {
      setActiveThreadId(payload.threads[0].id);
    }
  }

  async function createThread() {
    const response = await fetch("/api/chat-threads", {
      method: "POST",
      headers: buildAuthHeaders(token),
      body: JSON.stringify({ title: "New chat" }),
    });
    if (!response.ok) {
      return;
    }
    const payload = await readResponsePayload(response);
    await loadThreads();
    setActiveThreadId(payload.thread.id);
    setThreadDetails({ summaries: [], messages: [], thread: payload.thread });
  }

  async function loadThread(threadId) {
    const response = await fetch(`/api/chat-threads/${threadId}`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) {
      return;
    }
    const payload = await readResponsePayload(response);
    setThreadDetails(payload);
  }

  async function sendMessage(text) {
    const trimmed = text.trim();
    if (!trimmed || chatLoading) {
      return;
    }

    setChatLoading(true);
    setDraft("");

    let threadId = activeThreadId;
    if (!threadId) {
      const response = await fetch("/api/chat-threads", {
        method: "POST",
        headers: buildAuthHeaders(token),
        body: JSON.stringify({ title: "New chat" }),
      });
      if (!response.ok) {
        setChatLoading(false);
        return;
      }
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
      if (!response.ok) {
        throw new Error(payload.error || "FixMate AI could not save that message.");
      }
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
    const response = await fetch("/api/admin/files", {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) {
      return;
    }
    const payload = await readResponsePayload(response);
    setAdminFiles(payload.files);
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
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
      const payload = await readResponsePayload(response);
      if (!response.ok) {
        throw new Error(payload.error || "Authentication failed");
      }
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
    if (!file) {
      return;
    }

    setUploading(true);
    setAdminMessage("");
    try {
      const formData = new FormData();
      formData.append("file", file);
      const response = await fetch("/api/admin/files", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
        },
        body: formData,
      });
      const payload = await readResponsePayload(response);
      if (!response.ok) {
        throw new Error(payload.error || "Upload failed");
      }
      setAdminMessage(`Indexed ${payload.file.original_name}`);
      await loadAdminFiles();
    } catch (error) {
      setAdminMessage(error.message || "Upload failed");
    } finally {
      setUploading(false);
      event.target.value = "";
    }
  }

  async function deleteFile(fileId) {
    const response = await fetch(`/api/admin/files/${fileId}`, {
      method: "DELETE",
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });
    const payload = await readResponsePayload(response);
    if (!response.ok) {
      setAdminMessage(payload.error || "Delete failed");
      return;
    }
    setAdminMessage(`Deleted ${payload.deleted.original_name}`);
    await loadAdminFiles();
  }

  async function reindexFiles() {
    const response = await fetch("/api/admin/reindex", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });
    const payload = await readResponsePayload(response);
    if (!response.ok) {
      setAdminMessage(payload.error || "Reindex failed");
      return;
    }
    setAdminMessage(`Reindexed ${payload.documents_indexed} documents`);
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
                  onClick={() => {
                    setAuthMode(mode.key);
                    setAuthError("");
                  }}
                >
                  {mode.label}
                </button>
              ))}
            </div>

            <form className="auth-form" onSubmit={handleAuthSubmit}>
              {authMode === "register" ? (
                <label>
                  <span>Name</span>
                  <input
                    value={authForm.name}
                    onChange={(event) => setAuthForm((current) => ({ ...current, name: event.target.value }))}
                    placeholder="Your full name"
                  />
                </label>
              ) : null}

              <label>
                <span>{authMode === "admin" ? "Admin username" : "Email"}</span>
                <input
                  value={authForm.identifier}
                  onChange={(event) => setAuthForm((current) => ({ ...current, identifier: event.target.value }))}
                  placeholder={authMode === "admin" ? "admin" : "you@example.com"}
                />
              </label>

              <label>
                <span>Password</span>
                <input
                  type="password"
                  value={authForm.password}
                  onChange={(event) => setAuthForm((current) => ({ ...current, password: event.target.value }))}
                  placeholder="Password"
                />
              </label>

              {authError ? <p className="form-error">{authError}</p> : null}

              <button type="submit" className="primary-button" disabled={authLoading}>
                {authLoading ? "Please wait" : authMode === "register" ? "Create account" : "Sign in"}
              </button>
            </form>
          </section>
        </main>
      </div>
    );
  }

  if (isAdmin) {
    return (
      <div className="dashboard-shell admin-shell">
        <aside className="sidebar-panel admin-panel">
          <p className="eyebrow">Administrator</p>
          <h2>FixMate AI Console</h2>
          <p className="sidebar-copy">Upload support files, inspect the indexed library, and trigger reindexing when documents change.</p>
          <label className="upload-card">
            <span>{uploading ? "Uploading..." : "Upload file"}</span>
            <input type="file" onChange={handleUpload} disabled={uploading} />
          </label>
          <button type="button" className="secondary-button" onClick={reindexFiles}>
            Reindex library
          </button>
          <button type="button" className="ghost-button" onClick={logout}>
            Log out
          </button>
          {adminMessage ? <p className="status-text">{adminMessage}</p> : null}
        </aside>

        <main className="content-panel admin-content">
          <header className="panel-header">
            <div>
              <p className="chat-title">Uploaded Knowledge Files</p>
              <p className="chat-subtitle">Anything uploaded here becomes searchable for users after indexing.</p>
            </div>
          </header>

          <div className="file-grid">
            {adminFiles.map((file) => (
              <article key={file.id} className="file-card">
                <div>
                  <p className="file-name">{file.original_name}</p>
                  <p className="file-meta">Uploaded by {file.uploaded_by}</p>
                  <p className="file-meta">{Math.max(1, Math.round(file.size_bytes / 1024))} KB</p>
                </div>
                <button type="button" className="danger-button" onClick={() => void deleteFile(file.id)}>
                  Delete
                </button>
              </article>
            ))}
            {adminFiles.length === 0 ? <p className="empty-state">No uploaded files yet.</p> : null}
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="dashboard-shell">
      <aside className="sidebar-panel">
        <div>
          <p className="eyebrow">User Workspace</p>
          <h2>FixMate AI</h2>
          <p className="sidebar-copy">Saved chats stay here. Older turns are summarized automatically every 20 messages.</p>
        </div>

        <button type="button" className="primary-button" onClick={() => void createThread()}>
          New chat
        </button>

        <div className="thread-list">
          {threads.map((thread) => (
            <button
              key={thread.id}
              type="button"
              className={classNames("thread-item", activeThreadId === thread.id && "active")}
              onClick={() => setActiveThreadId(thread.id)}
            >
              <span>{thread.title}</span>
            </button>
          ))}
          {threads.length === 0 ? <p className="empty-state">No chats yet. Start a new one.</p> : null}
        </div>

        <button type="button" className="ghost-button" onClick={logout}>
          Log out
        </button>
      </aside>

      <main className="content-panel">
        <header className="panel-header">
          <div>
            <p className="chat-title">Welcome, {currentUser.name}</p>
            <p className="chat-subtitle">Ask about appliance support or any admin-uploaded documents.</p>
          </div>
          <span className={classNames("status-pill", chatLoading ? "busy" : "idle")}>{chatLoading ? "Thinking" : "Online"}</span>
        </header>

        {!activeThreadId ? (
          <section className="welcome-card">
            <p className="chat-subtitle">Start with one of these prompts</p>
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
            <div className="summary-strip">
              {threadDetails.summaries.map((summary) => (
                <article key={summary.id} className="summary-card">
                  <p className="message-role">Summary</p>
                  <p className="message-text">{summary.summary}</p>
                </article>
              ))}
            </div>

            <div className="message-list workspace-messages">
              {threadDetails.messages.map((message) => (
                <article key={message.id} className={classNames("message-card", message.role, message.tone)}>
                  <p className="message-role">{message.role === "assistant" ? "FixMate AI" : "You"}</p>
                  <p className="message-text">{message.content}</p>
                </article>
              ))}
            </div>

            <form className="composer" onSubmit={(event) => {
              event.preventDefault();
              void sendMessage(draft);
            }}>
              <textarea
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="Ask about an uploaded file or appliance issue"
                rows={3}
              />
              <button type="submit" disabled={!canSend}>Send</button>
            </form>
          </section>
        )}
      </main>
    </div>
  );
}
