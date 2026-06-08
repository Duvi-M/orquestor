const API_BASE = window.API_BASE || "http://127.0.0.1:9000";

const sessions = new Map();
let activeSessionId = null;

const createBtn = document.getElementById("create-session");
const sessionsEl = document.getElementById("sessions");
const activeSessionEl = document.getElementById("active-session");
const eventsEl = document.getElementById("events");
const form = document.getElementById("message-form");
const input = document.getElementById("message");
const sendBtn = document.getElementById("send");
const historyBtn = document.getElementById("load-history");
const novncLink = document.getElementById("novnc-link");
const clearSessionsBtn = document.getElementById("clear-sessions");
const sessionStatusEl = document.getElementById("session-status");
const STORAGE_KEY = "computer-use-orchestrator.sessions";

const EVENT_LABELS = {
  assistant_block: "ASSISTANT_BLOCK",
  user_message: "USER_MESSAGE",
  tool_use_start: "TOOL_USE_START",
  tool_result: "TOOL_RESULT",
  screenshot: "SCREENSHOT",
  done: "DONE",
  ready: "READY",
  error: "ERROR",
  ping: "PING",
};

function shortId(id) {
  return id ? id.slice(0, 8) : "none";
}

function eventClass(name) {
  if (name === "error") return "error";
  if (name === "assistant_block") return "assistant";
  if (name === "user_message") return "user";
  if (name === "done" || name === "ready") return "ready";
  if (name === "ping") return "muted";
  return "tool";
}

function eventLabel(name) {
  return EVENT_LABELS[name] || name.toUpperCase();
}

function statusClass(status) {
  if (status === "done" || status === "completed" || status === "ready") return "ready";
  if (status === "running") return "running";
  if (status === "error") return "error";
  if (status === "deleted") return "muted";
  return "muted";
}

function describeEvent(name, data) {
  if (name === "assistant_block") return data.text || "";
  if (name === "user_message") return data.text || "";
  if (name === "tool_use_start") return `${data.name || "tool"} ${JSON.stringify(data.input || {})}`;
  if (name === "tool_result") return data.error || data.output || "tool completed";
  if (name === "screenshot") return "screen updated";
  if (name === "error") return data.message || data.error || "error";
  if (name === "done") return "task completed";
  if (name === "ready") return "stream connected";
  return JSON.stringify(data);
}

function renderEmptyState(title, body) {
  eventsEl.innerHTML = "";
  const empty = document.createElement("div");
  empty.className = "empty-state";

  const heading = document.createElement("strong");
  heading.textContent = title;

  const text = document.createElement("span");
  text.textContent = body;

  empty.append(heading, text);
  eventsEl.appendChild(empty);
}

function renderEventRow(name, data, fromHistory = false) {
  const empty = eventsEl.querySelector(".empty-state");
  if (empty) empty.remove();

  const row = document.createElement("div");
  row.className = `event ${eventClass(name)}`;

  const label = document.createElement("strong");
  label.textContent = fromHistory ? `${eventLabel(name)} · HISTORY` : eventLabel(name);

  const body = document.createElement("span");
  body.textContent = describeEvent(name, data);

  row.append(label, body);
  eventsEl.appendChild(row);
  eventsEl.scrollTop = eventsEl.scrollHeight;
}

function renderSessions() {
  sessionsEl.innerHTML = "";
  if (sessions.size === 0) {
    const empty = document.createElement("div");
    empty.className = "sidebar-empty";
    empty.textContent = "No sessions yet";
    sessionsEl.appendChild(empty);
    return;
  }
  for (const session of sessions.values()) {
    const button = document.createElement("button");
    const status = session.status || "created";
    button.className = `session ${session.id === activeSessionId ? "active" : ""}`;
    button.innerHTML = "";
    const title = document.createElement("span");
    title.textContent = shortId(session.id);
    const badge = document.createElement("span");
    badge.className = `status-badge ${statusClass(status)}`;
    badge.textContent = status;
    button.append(title, badge);
    button.onclick = () => setActiveSession(session.id);
    sessionsEl.appendChild(button);
  }
}

function persistSessions() {
  const serializable = [...sessions.values()].map((session) => ({
    id: session.id,
    novncUrl: session.novncUrl,
    status: session.status,
  }));
  localStorage.setItem(STORAGE_KEY, JSON.stringify(serializable));
}

function removeSession(sessionId) {
  const session = sessions.get(sessionId);
  if (session?.eventSource) {
    session.eventSource.close();
  }
  sessions.delete(sessionId);
  if (activeSessionId === sessionId) {
    activeSessionId = null;
    setActiveSession(sessions.keys().next().value || null);
  }
  persistSessions();
  renderSessions();
}

function restoreSessions() {
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return;

  try {
    const saved = JSON.parse(raw);
    for (const item of saved) {
      if (!item.id || !item.novncUrl) continue;
      const session = {
        id: item.id,
        novncUrl: item.novncUrl,
        status: item.status || "restored",
        events: [],
        eventSource: null,
      };
      sessions.set(item.id, session);
    }
    const first = sessions.keys().next().value;
    if (first) setActiveSession(first);
  } catch {
    localStorage.removeItem(STORAGE_KEY);
  }
}

function addEvent(sessionId, name, data, fromHistory = false) {
  const session = sessions.get(sessionId);
  if (!session) return;

  session.events.push({ name, data });
  if (name === "done") session.status = "done";
  if (name === "error") session.status = "error";
  if (name === "user_message") session.status = "running";
  persistSessions();

  if (sessionId !== activeSessionId) {
    renderSessions();
    return;
  }

  renderEventRow(name, data, fromHistory);
  renderSessions();
}

function setActiveSession(sessionId) {
  if (activeSessionId && activeSessionId !== sessionId) {
    const previous = sessions.get(activeSessionId);
    if (previous?.eventSource) {
      previous.eventSource.close();
      previous.eventSource = null;
    }
  }

  activeSessionId = sessionId;
  const session = sessions.get(sessionId);

  activeSessionEl.textContent = session ? session.id : "None";
  const status = session ? session.status || "created" : "idle";
  sessionStatusEl.textContent = status;
  sessionStatusEl.className = `status-badge ${statusClass(status)}`;
  input.disabled = !session;
  sendBtn.disabled = !session;
  historyBtn.disabled = !session;

  if (session) {
    novncLink.href = session.novncUrl;
    novncLink.classList.remove("disabled");
    ensureSessionExists(session);
  } else {
    novncLink.href = "#";
    novncLink.classList.add("disabled");
  }

  if (session) {
    eventsEl.innerHTML = "";
    if (session.events.length === 0) {
      renderEmptyState("Waiting for events", "Send a task or open history for this session.");
    } else {
      for (const event of session.events) {
        renderEventRow(event.name, event.data);
      }
    }
  } else {
    renderEmptyState("No active session", "Create a session to stream worker events.");
  }
  renderSessions();
}

async function ensureSessionExists(session) {
  try {
    const response = await fetch(`${API_BASE}/sessions/${session.id}`);
    if (response.status === 404) {
      removeSession(session.id);
      return false;
    }
    if (!response.ok) return false;
    connectEvents(session);
    return true;
  } catch (error) {
    addEvent(session.id, "error", { message: `Session check failed: ${error.message}` });
    return false;
  }
}

function connectEvents(session) {
  if (session.eventSource) {
    session.eventSource.close();
  }

  session.eventSource = new EventSource(`${API_BASE}/sessions/${session.id}/events`);

  session.eventSource.onerror = () => {
    addEvent(session.id, "error", { message: "SSE connection lost or session no longer exists" });
    if (session.eventSource) {
      session.eventSource.close();
      session.eventSource = null;
    }
  };

  const names = [
    "ready",
    "user_message",
    "assistant_block",
    "tool_use_start",
    "tool_result",
    "screenshot",
    "done",
    "error",
    "ping",
  ];

  for (const name of names) {
    session.eventSource.addEventListener(name, (event) => {
      const data = event.data ? JSON.parse(event.data) : {};
      addEvent(session.id, name, data);
    });
  }
}

async function createSession() {
  createBtn.disabled = true;
  createBtn.textContent = "Creating...";
  try {
    const response = await fetch(`${API_BASE}/sessions`, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Failed to create session");

    const session = {
      id: data.session_id,
      novncUrl: data.novnc_url,
      status: "ready",
      events: [],
      eventSource: null,
    };

    sessions.set(session.id, session);
    persistSessions();
    connectEvents(session);
    setActiveSession(session.id);
  } catch (error) {
    alert(error.message);
  } finally {
    createBtn.disabled = false;
    createBtn.textContent = "New Session";
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const session = sessions.get(activeSessionId);
  const text = input.value.trim();
  if (!session || !text) return;

  input.value = "";
  sendBtn.disabled = true;
  sendBtn.textContent = "Sending...";

  try {
    const exists = await ensureSessionExists(session);
    if (!exists) throw new Error("Session no longer exists");

    const response = await fetch(`${API_BASE}/sessions/${session.id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Failed to send message");
    session.status = "running";
    persistSessions();
    renderSessions();
  } catch (error) {
    addEvent(session.id, "error", { message: error.message });
  } finally {
    sendBtn.disabled = false;
    sendBtn.textContent = "Send";
  }
}

async function loadHistory() {
  const session = sessions.get(activeSessionId);
  if (!session) return;

  const response = await fetch(`${API_BASE}/sessions/${session.id}/history`);
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 404) {
      removeSession(session.id);
      return;
    }
    addEvent(session.id, "error", { message: data.detail || "Failed to load history" });
    return;
  }

  session.events = [];
  session.status = data.session.status;
  persistSessions();
  eventsEl.innerHTML = "";
  if (data.events.length === 0) {
    renderEmptyState("No history yet", "Events will appear here after a task runs.");
  } else {
    for (const event of data.events) {
      addEvent(session.id, event.event, event.data, true);
    }
  }
  renderSessions();
}

function clearLocalSessions() {
  for (const session of sessions.values()) {
    if (session.eventSource) {
      session.eventSource.close();
    }
  }
  sessions.clear();
  activeSessionId = null;
  localStorage.removeItem(STORAGE_KEY);
  setActiveSession(null);
}

createBtn.onclick = createSession;
historyBtn.onclick = loadHistory;
clearSessionsBtn.onclick = clearLocalSessions;
form.onsubmit = sendMessage;
restoreSessions();
