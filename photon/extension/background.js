// The extension's only door to Photon.
//
// Every API call goes through here, never from the Meet page itself: the
// token must not be readable by a script running in meet.google.com's
// context, and a fetch from the service worker is not subject to the page's
// CORS. The content script only ever hands over caption lines; the side
// panel only ever asks for this tab's suggestions.
//
// Service workers are killed when idle, so per-tab state lives in
// chrome.storage.session rather than in variables.

const PAIRING = "pairing"; // {apiBase, token, workspaceId, workspaceName, email}

chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});

async function pairing() {
  return (await chrome.storage.local.get(PAIRING))[PAIRING] || null;
}

async function tabState(tabId) {
  const key = `tab:${tabId}`;
  return (await chrome.storage.session.get(key))[key] || null;
}

async function setTabState(tabId, patch) {
  const key = `tab:${tabId}`;
  const next = { ...((await tabState(tabId)) || {}), ...patch };
  await chrome.storage.session.set({ [key]: next });
  return next;
}

class Unpaired extends Error {}

async function api(path, { method = "GET", body } = {}) {
  const p = await pairing();
  if (!p) throw new Unpaired("not paired");
  const res = await fetch(`${p.apiBase}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${p.token}`,
      "X-Workspace-Id": p.workspaceId,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    // Revoked in Photon, or expired: forget it, so the panel asks to pair
    // again instead of failing every few seconds forever.
    await chrome.storage.local.remove(PAIRING);
    throw new Unpaired("this browser was disconnected from Photon");
  }
  if (!res.ok) {
    let detail = `Photon returned ${res.status}`;
    try {
      detail = (await res.json()).detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

async function pair(apiBase, code) {
  const base = apiBase.replace(/\/+$/, "");
  const res = await fetch(`${base}/api/extension/pair`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, device_name: navigator.userAgent.includes("Edg/") ? "Edge" : "Chrome" }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `Pairing failed (${res.status})`);
  const p = { apiBase: base, token: data.token, workspaceId: data.workspace_id,
              workspaceName: data.workspace_name, email: data.email };
  await chrome.storage.local.set({ [PAIRING]: p });
  return { email: p.email, workspaceName: p.workspaceName };
}

/** The whisper session + your private thread for the Meet in this tab. */
async function joinMeeting(tabId, url, title) {
  const current = await tabState(tabId);
  if (current && current.url === url && current.threadId) return current;
  const s = await api("/api/extension/meet-session", { method: "POST", body: { meet_url: url, title } });
  const thread = await api(`/api/whisper/sessions/${s.session_id}/thread`);
  return setTabState(tabId, { url, sessionId: s.session_id, threadId: thread.id, heard: 0 });
}

async function sendLines(tabId, lines) {
  const st = await tabState(tabId);
  if (!st || !st.sessionId) return { sent: 0 };
  let sent = 0;
  for (const line of lines) {
    await api(`/api/whisper/sessions/${st.sessionId}/lines`, {
      method: "POST",
      // Your own captions are "ours" by construction. Everyone else's is
      // left for the server to resolve against the workspace's members —
      // it knows who your colleagues are; this page only sees names.
      body: { speaker_name: line.speaker, text: line.text, ...(line.self ? { is_client: false } : {}) },
    });
    sent += 1;
  }
  await setTabState(tabId, { heard: (st.heard || 0) + lines.filter((l) => !l.self).length });
  return { sent };
}

async function handle(msg, sender) {
  const tabId = msg.tabId ?? sender.tab?.id;
  switch (msg.type) {
    case "pair":
      return pair(msg.apiBase, msg.code);
    case "unpair":
      await chrome.storage.local.remove(PAIRING);
      return { ok: true };
    case "status": {
      const p = await pairing();
      return { paired: Boolean(p), email: p?.email, workspaceName: p?.workspaceName,
               apiBase: p?.apiBase, tab: tabId != null ? await tabState(tabId) : null };
    }
    case "meeting":
      return joinMeeting(tabId, msg.url, msg.title);
    case "captions":
      await setTabState(tabId, { captionsOn: msg.on, strategy: msg.strategy });
      return { ok: true };
    case "lines":
      return sendLines(tabId, msg.lines);
    case "messages": {
      const st = await tabState(tabId);
      return st?.threadId ? api(`/api/whisper/threads/${st.threadId}/messages`) : [];
    }
    case "ask": {
      const st = await tabState(tabId);
      if (!st?.threadId) throw new Error("Not in a meeting yet");
      return api(`/api/whisper/threads/${st.threadId}/ask`, { method: "POST", body: { question: msg.question } });
    }
    default:
      throw new Error(`unknown message ${msg.type}`);
  }
}

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  handle(msg, sender)
    .then((data) => reply({ ok: true, data }))
    .catch((err) => reply({ ok: false, unpaired: err instanceof Unpaired, error: String(err.message || err) }));
  return true; // async reply
});

chrome.tabs.onRemoved.addListener((tabId) => {
  chrome.storage.session.remove(`tab:${tabId}`);
});
