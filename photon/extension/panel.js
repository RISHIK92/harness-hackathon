// The side panel beside Google Meet: pairing, then this tab's suggestions.
//
// It polls rather than subscribing, like the whisper page in Photon — a
// suggestion is produced because the CLIENT spoke, not because anything
// happened here. Two seconds is the same cadence the web panel uses.
"use strict";

const POLL_MS = 2000;
const $ = (id) => document.getElementById(id);
let meetTabId = null;
let lastRender = "";

const call = (msg) =>
  new Promise((resolve) => chrome.runtime.sendMessage(msg, (r) => resolve(r || { ok: false, error: "no reply" })));

// Citation markers are for the evidence chips in Photon, not for prose the
// rep is about to say out loud. Same rule as the web panel.
const stripCitations = (t) => (t || "").replace(/\s*\[ev_[0-9a-f]+(?:\s*,\s*ev_[0-9a-f]+)*\]/g, "");

function show(section) {
  for (const id of ["pair", "notMeet", "meet"]) $(id).hidden = id !== section;
}

async function activeMeetTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab && /^https:\/\/meet\.google\.com\/[a-z]{3}-[a-z]{4}-[a-z]{3}/i.test(tab.url || "") ? tab.id : null;
}

function render(messages) {
  const key = JSON.stringify(messages.map((m) => [m.id, m.text]));
  if (key === lastRender) return;
  lastRender = key;
  const list = $("messages");
  list.textContent = "";
  for (const m of messages) {
    const li = document.createElement("li");
    li.className = m.role;
    if (m.trigger_text) {
      const t = document.createElement("div");
      t.className = "trigger";
      t.textContent = `client asked: ${m.trigger_text}`;
      li.append(t);
    }
    const p = document.createElement("p");
    p.className = "text";
    p.textContent = stripCitations(m.text);
    li.append(p);
    if (m.role !== "member") {
      // The warning comes with the answer because it decides whether the
      // next sentence out of the rep's mouth is safe to say.
      if (!m.warnings || !m.warnings.length) {
        const ok = document.createElement("p");
        ok.className = "safe";
        ok.textContent = "✅ Safe to say as-is";
        li.append(ok);
      } else {
        for (const w of m.warnings) {
          const el = document.createElement("p");
          el.className = "warning";
          const b = document.createElement("b");
          b.textContent = `⚠️ ${w.label}`;
          el.append(b, ` — ${w.detail}`);
          li.append(el);
        }
      }
    }
    list.append(li);
  }
  $("empty").hidden = messages.length > 0;
  list.lastElementChild?.scrollIntoView({ block: "end" });
}

async function refresh() {
  meetTabId = await activeMeetTab();
  const status = await call({ type: "status", tabId: meetTabId });
  const s = status.data || {};
  $("footer").hidden = !s.paired;
  if (!s.paired) {
    $("status").textContent = "not connected";
    show("pair");
    return;
  }
  $("who").textContent = `${s.email} · ${s.workspaceName}`;
  if (meetTabId == null) {
    $("status").textContent = "connected";
    show("notMeet");
    return;
  }
  show("meet");
  const tab = s.tab || {};
  $("captionsOff").hidden = tab.captionsOn !== false;
  $("status").textContent = tab.sessionId
    ? `listening · ${tab.heard || 0} line${tab.heard === 1 ? "" : "s"} heard`
    : "waiting for the call to start";
  if (tab.threadId) {
    const r = await call({ type: "messages", tabId: meetTabId });
    if (r.ok) render(r.data);
  }
}

$("pairBtn").addEventListener("click", async () => {
  const apiBase = $("apiBase").value.trim().replace(/\/+$/, "") || "http://localhost:8000";
  const code = $("code").value.trim();
  $("pairError").hidden = true;
  let origin;
  try {
    origin = new URL(apiBase).origin;
  } catch {
    $("pairError").textContent = "That address isn't a URL";
    $("pairError").hidden = false;
    return;
  }
  // Access to Photon's address is asked for here, at the moment the user
  // names it, rather than requested for every site up front.
  const granted = await chrome.permissions.request({ origins: [`${origin}/*`] });
  if (!granted) {
    $("pairError").textContent = "Photon needs permission to reach that address";
    $("pairError").hidden = false;
    return;
  }
  $("pairBtn").disabled = true;
  const r = await call({ type: "pair", apiBase, code });
  $("pairBtn").disabled = false;
  if (!r.ok) {
    $("pairError").textContent = r.error;
    $("pairError").hidden = false;
    return;
  }
  $("code").value = "";
  void refresh();
});

$("unpair").addEventListener("click", async () => {
  await call({ type: "unpair" });
  void refresh();
});

$("askForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("ask").value.trim();
  if (!q || meetTabId == null) return;
  $("ask").value = "";
  $("ask").disabled = true;
  await call({ type: "ask", tabId: meetTabId, question: q });
  $("ask").disabled = false;
  $("ask").focus();
  void refresh();
});

chrome.tabs.onActivated.addListener(() => void refresh());
void refresh();
setInterval(refresh, POLL_MS);
