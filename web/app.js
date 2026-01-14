const API_BASE = "http://127.0.0.1:9000";

let sessionId = null;
let eventSource = null;
let lastEventId = null;

const log = document.getElementById("log");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const iframe = document.getElementById("ui");

function addLog(text, cls) {
  const div = document.createElement("div");
  div.className = `event ${cls}`;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

async function createSession() {
  const res = await fetch(`${API_BASE}/sessions`, {
    method: "POST"
  });
  const data = await res.json();

  sessionId = data.session_id;

  addLog(`Session created: ${sessionId}`, "ready");

  iframe.src = data.ui_url;

  connectSSE();
}

function connectSSE() {
  let url = `${API_BASE}/sessions/${sessionId}/events`;
  if (lastEventId) {
    url += "";
  }

  eventSource = new EventSource(url);

  eventSource.onmessage = (e) => {
    lastEventId = e.lastEventId || lastEventId;
  };

  eventSource.addEventListener("ready", (e) => {
    addLog("SSE connected", "ready");
  });

  eventSource.addEventListener("user_message", (e) => {
    const data = JSON.parse(e.data);
    addLog(`User: ${data.text}`, "user");
  });

  eventSource.addEventListener("assistant_block", (e) => {
    const data = JSON.parse(e.data);
    addLog(`Assistant: ${data.text}`, "assistant");
  });

  eventSource.addEventListener("done", () => {
    addLog("Done", "ready");
  });

  eventSource.addEventListener("error", (e) => {
    addLog("Error event received", "error");
  });

  eventSource.addEventListener("ping", () => {
    addLog("ping", "ping");
  });
}

async function sendMessage() {
  const text = input.value.trim();
  if (!text) return;

  input.value = "";

  await fetch(`${API_BASE}/sessions/${sessionId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text })
  });
}

sendBtn.onclick = sendMessage;
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendMessage();
});

createSession();