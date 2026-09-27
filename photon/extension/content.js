// Runs inside meet.google.com. Reads the live captions and hands settled
// lines to the service worker — nothing else. It never holds the Photon
// token and never talks to Photon directly (see background.js).
//
// It starts sending only once you are actually IN the call: the lobby shares
// the meeting's URL, and a session made from the green room would start the
// record before anyone had spoken.
(function () {
  "use strict";
  const { CaptionTracker, readCaptionBlocks, isSelf } = self.PhotonCaptions;

  const TICK_MS = 400;
  const MEET_PATH = /^\/[a-z]{3}-[a-z]{4}-[a-z]{3}(?:$|[/?])/i;

  const tracker = new CaptionTracker();
  const ids = new WeakMap(); // caption block element -> stable id
  let nextId = 1;
  let live = new Set();      // ids seen on the previous tick
  let joined = false;
  let joining = false;
  let lastJoinAttempt = 0;
  let lastCaptionState = null;

  const send = (msg) =>
    new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(msg, (reply) => resolve(reply || { ok: false }));
      } catch {
        resolve({ ok: false }); // extension reloaded under us; this page is orphaned
      }
    });

  function inCall() {
    if (!MEET_PATH.test(location.pathname)) return false;
    // The leave button exists only once you are in; captions exist only in a call.
    return Boolean(
      document.querySelector('[aria-label*="Leave call" i], [aria-label*="leave the call" i]') ||
        readCaptionBlocks(document).region,
    );
  }

  async function ensureJoined(now) {
    if (joined || joining || now - lastJoinAttempt < 10000) return;
    joining = true;
    lastJoinAttempt = now;
    const title = (document.title || "").replace(/^Meet\s*[-–]\s*/i, "").trim();
    const reply = await send({ type: "meeting", url: location.origin + location.pathname, title });
    // Not paired yet (or Photon unreachable): retried every 10s, so pairing
    // mid-call starts listening without reloading Meet.
    joined = Boolean(reply.ok);
    joining = false;
  }

  function tick() {
    const now = Date.now();
    if (!inCall()) return;
    void ensureJoined(now);

    const { strategy, region, blocks } = readCaptionBlocks(document);
    const state = `${region}:${strategy}`;
    if (state !== lastCaptionState) {
      lastCaptionState = state;
      void send({ type: "captions", on: region, strategy });
    }

    const seen = new Set();
    for (const b of blocks) {
      let id = ids.get(b.el);
      if (!id) ids.set(b.el, (id = `b${nextId++}`));
      seen.add(id);
      tracker.observe(id, b.speaker, b.text, now);
    }
    const out = [];
    for (const id of live) if (!seen.has(id)) out.push(...tracker.remove(id, now));
    live = seen;
    out.push(...tracker.flush(now));

    if (out.length && joined) {
      void send({ type: "lines", lines: out.map((l) => ({ ...l, self: isSelf(l.speaker) })) });
    }
  }

  setInterval(tick, TICK_MS);
})();
