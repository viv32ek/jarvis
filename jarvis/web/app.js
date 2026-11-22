(() => {
  const messagesEl = document.getElementById("messages");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const sendBtn = document.getElementById("send-btn");
  const micBtn = document.getElementById("mic-btn");
  const toolStatusEl = document.getElementById("tool-status");
  const statusDot = document.getElementById("status-dot");
  const statusText = document.getElementById("status-text");
  const modelSelect = document.getElementById("model-select");
  const resetBtn = document.getElementById("reset-btn");
  const ttsToggle = document.getElementById("tts-toggle");
  const factsList = document.getElementById("facts-list");
  const remindersList = document.getElementById("reminders-list");
  const factForm = document.getElementById("fact-form");
  const factInput = document.getElementById("fact-input");
  const reminderForm = document.getElementById("reminder-form");
  const reminderInput = document.getElementById("reminder-input");
  const modeAssistantBtn = document.getElementById("mode-assistant");
  const modeCodeBtn = document.getElementById("mode-code");
  const workspaceBox = document.getElementById("workspace-box");
  const workspaceForm = document.getElementById("workspace-form");
  const workspaceInput = document.getElementById("workspace-input");

  let ws = null;
  let activeAssistantEl = null;
  let activeText = "";
  let busy = false;
  let currentMode = "assistant";

  function setBusy(state) {
    busy = state;
    sendBtn.disabled = state;
    if (!state) {
      toolStatusEl.classList.add("hidden");
      toolStatusEl.textContent = "";
    }
  }

  function renderTextWithCode(container, text) {
    container.innerHTML = "";
    const fenceRegex = /```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g;
    let lastIndex = 0;
    let match;

    while ((match = fenceRegex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        appendInlineText(container, text.slice(lastIndex, match.index));
      }
      const lang = match[1] || "code";
      const code = match[2];
      const wrapper = document.createElement("div");
      wrapper.className = "code-block";

      const header = document.createElement("div");
      header.className = "code-header";
      const label = document.createElement("span");
      label.textContent = lang;
      const copyBtn = document.createElement("button");
      copyBtn.type = "button";
      copyBtn.className = "btn-ghost";
      copyBtn.textContent = "Copy";
      copyBtn.onclick = async () => {
        try {
          await navigator.clipboard.writeText(code);
          copyBtn.textContent = "Copied";
          setTimeout(() => (copyBtn.textContent = "Copy"), 1200);
        } catch {
          copyBtn.textContent = "Failed";
        }
      };
      header.append(label, copyBtn);

      const pre = document.createElement("pre");
      const codeEl = document.createElement("code");
      codeEl.textContent = code;
      pre.appendChild(codeEl);
      wrapper.append(header, pre);
      container.appendChild(wrapper);

      lastIndex = fenceRegex.lastIndex;
    }

    if (lastIndex < text.length) {
      appendInlineText(container, text.slice(lastIndex));
    }
  }

  function appendInlineText(container, text) {
    const parts = text.split(/(`[^`]+`)/g);
    for (const part of parts) {
      if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
        const code = document.createElement("code");
        code.className = "inline-code";
        code.textContent = part.slice(1, -1);
        container.appendChild(code);
      } else if (part) {
        container.appendChild(document.createTextNode(part));
      }
    }
  }

  function applyModeUI(mode, model, workspace) {
    currentMode = mode;
    modeAssistantBtn.classList.toggle("active", mode === "assistant");
    modeCodeBtn.classList.toggle("active", mode === "code");
    workspaceBox.classList.toggle("hidden", mode !== "code");
    if (model) {
      modelSelect.value = model;
    }
    if (workspace) {
      workspaceInput.value = workspace;
    }
    chatInput.placeholder =
      mode === "code"
        ? "Code task for Gemini 4 Neon… (staging deploys always ask permission)"
        : "Ask Jarvis anything… (or /help)";
  }

  function appendMessage(role, content, channel = "web") {
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    const meta = document.createElement("div");
    meta.className = "msg-meta";
    meta.textContent = role === "user" ? `You · ${channel}` : role === "error" ? "Error" : "Jarvis";
    const body = document.createElement("div");
    body.className = "msg-body";
    if (role === "assistant") {
      renderTextWithCode(body, content);
    } else {
      body.textContent = content;
    }
    div.appendChild(meta);
    div.appendChild(body);
    messagesEl.appendChild(div);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return body;
  }

  function speak(text) {
    if (!ttsToggle.checked || currentMode === "code" || !("speechSynthesis" in window) || !text) return;
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance(text);
    utter.rate = 1.05;
    window.speechSynthesis.speak(utter);
  }

  function connectWS() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws/chat`);

    ws.onopen = () => {
      statusDot.classList.add("online");
      statusText.textContent = "Online · Connected";
    };

    ws.onclose = () => {
      statusDot.classList.remove("online");
      statusText.textContent = "Reconnecting…";
      setBusy(false);
      setTimeout(connectWS, 2000);
    };

    ws.onmessage = (ev) => {
      const data = JSON.parse(ev.data);
      if (data.kind === "delta") {
        if (!activeAssistantEl) {
          activeAssistantEl = appendMessage("assistant", "");
          activeText = "";
        }
        activeText += data.text || "";
        activeAssistantEl.textContent = activeText;
        messagesEl.scrollTop = messagesEl.scrollHeight;
      } else if (data.kind === "tool" && data.tool_name) {
        toolStatusEl.textContent = `Jarvis is running: ${data.tool_name}…`;
        toolStatusEl.classList.remove("hidden");
      } else if (data.kind === "result") {
        const finalText = activeText || data.text || "";
        if (!activeAssistantEl && finalText) {
          activeAssistantEl = appendMessage("assistant", finalText);
        } else if (activeAssistantEl) {
          renderTextWithCode(activeAssistantEl, finalText);
        }
        speak(finalText.trim());
        activeAssistantEl = null;
        activeText = "";
        setBusy(false);
        refreshHealth();
        refreshMemory();
      } else if (data.kind === "error") {
        appendMessage("error", data.text || "Unknown error");
        activeAssistantEl = null;
        activeText = "";
        setBusy(false);
      }
    };
  }

  async function refreshHealth() {
    const res = await fetch("/api/health");
    if (!res.ok) return;
    const data = await res.json();
    applyModeUI(data.mode || "assistant", data.model, data.workspace);
  }

  async function refreshHistory(mode = currentMode) {
    const res = await fetch(`/api/history?limit=40&mode=${encodeURIComponent(mode)}`);
    if (!res.ok) return;
    const data = await res.json();
    messagesEl.innerHTML = "";
    if (!data.messages.length) {
      const greeting =
        mode === "code"
          ? "Code Mode ready (Gemini 4 Neon · auto edits, staging deploys ask permission)."
          : "Jarvis online. How can I help?";
      appendMessage("assistant", greeting);
      return;
    }
    for (const m of data.messages) {
      appendMessage(m.role, m.content, m.channel);
    }
  }

  async function refreshMemory() {
    const res = await fetch("/api/memory");
    if (!res.ok) return;
    const data = await res.json();

    remindersList.innerHTML = "";
    if (!data.reminders.length) {
      remindersList.innerHTML = '<li class="empty">No pending reminders</li>';
    } else {
      for (const r of data.reminders) {
        const li = document.createElement("li");
        const span = document.createElement("span");
        span.textContent = r.text;
        const btn = document.createElement("button");
        btn.className = "item-btn";
        btn.textContent = "✓";
        btn.title = "Mark done";
        btn.onclick = async () => {
          await fetch(`/api/memory/reminders/${r.id}/done`, { method: "POST" });
          refreshMemory();
        };
        li.append(span, btn);
        remindersList.appendChild(li);
      }
    }

    factsList.innerHTML = "";
    if (!data.facts.length) {
      factsList.innerHTML = '<li class="empty">No saved facts yet</li>';
    } else {
      for (const f of data.facts) {
        const li = document.createElement("li");
        const span = document.createElement("span");
        span.textContent = f.content;
        const btn = document.createElement("button");
        btn.className = "item-btn delete";
        btn.textContent = "×";
        btn.title = "Forget";
        btn.onclick = async () => {
          await fetch(`/api/memory/facts/${f.id}`, { method: "DELETE" });
          refreshMemory();
        };
        li.append(span, btn);
        factsList.appendChild(li);
      }
    }
  }

  function sendMessage(text) {
    const trimmed = text.trim();
    if (!trimmed || busy || !ws || ws.readyState !== WebSocket.OPEN) return;
    appendMessage("user", trimmed, currentMode === "code" ? "web · code" : "web");
    chatInput.value = "";
    setBusy(true);
    ws.send(JSON.stringify({ message: trimmed, channel: "web" }));
  }

  async function switchMode(mode) {
    const res = await fetch("/api/mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    if (!res.ok) return;
    const data = await res.json();
    applyModeUI(data.mode, data.model, data.workspace);
    await refreshHistory(data.mode);
  }

  modeAssistantBtn.addEventListener("click", () => switchMode("assistant"));
  modeCodeBtn.addEventListener("click", () => switchMode("code"));

  workspaceForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const path = workspaceInput.value.trim();
    if (!path) return;
    const res = await fetch("/api/workspace", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!res.ok) {
      const err = await res.json();
      appendMessage("error", err.detail || "Invalid workspace path");
      return;
    }
    const data = await res.json();
    workspaceInput.value = data.workspace;
    appendMessage("assistant", `Code workspace set to \`${data.workspace}\`.`);
  });

  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    sendMessage(chatInput.value);
  });

  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(chatInput.value);
    }
  });

  modelSelect.addEventListener("change", async () => {
    await fetch("/api/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: modelSelect.value }),
    });
  });

  resetBtn.addEventListener("click", async () => {
    await fetch("/api/reset", { method: "POST" });
    appendMessage("assistant", `Started a fresh ${currentMode} conversation. Long-term memory is preserved.`);
  });

  factForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const content = factInput.value.trim();
    if (!content) return;
    await fetch("/api/memory/facts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    factInput.value = "";
    refreshMemory();
  });

  reminderForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = reminderInput.value.trim();
    if (!text) return;
    await fetch("/api/memory/reminders", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    reminderInput.value = "";
    refreshMemory();
  });

  // Free browser push-to-talk via Web Speech API
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (SpeechRecognition) {
    const rec = new SpeechRecognition();
    rec.lang = "en-US";
    rec.interimResults = false;
    let listening = false;

    micBtn.addEventListener("click", () => {
      if (listening) {
        rec.stop();
        return;
      }
      listening = true;
      micBtn.classList.add("recording");
      rec.start();
    });

    rec.onresult = (e) => {
      const transcript = e.results[0][0].transcript;
      sendMessage(transcript);
    };

    rec.onend = () => {
      listening = false;
      micBtn.classList.remove("recording");
    };
  } else {
    micBtn.disabled = true;
    micBtn.title = "Browser SpeechRecognition not supported";
  }

  connectWS();
  refreshHealth().then(() => refreshHistory(currentMode));
  refreshMemory();
})();
