/* Reading Google Meet's live captions, and deciding when a line is finished.
 *
 * Two halves, kept apart so the hard one can be tested without a browser:
 *
 *   CaptionTracker  pure: fed (block, speaker, text, time) observations,
 *                   emits settled lines. No DOM.
 *   readCaptionBlocks  the DOM half: finds Meet's caption region and returns
 *                   one {el, speaker, text} per visible caption block.
 *
 * Why a tracker at all: Meet does not emit sentences. A caption block is one
 * speaker's turn, and its text GROWS as they talk and is REWRITTEN as the
 * recogniser changes its mind ("export" -> "exports time out"). Posting every
 * mutation would send each question a dozen times, half-formed; posting only
 * when the block disappears would answer the client a minute late. So a block
 * is read as it settles: once its text has not changed for SETTLE_MS, what is
 * new since the last emit from that block goes out as one line.
 *
 * Loaded as a classic script before content.js (MV3 content scripts are not
 * modules); exported for node's test runner when `module` exists.
 */
(function (root) {
  "use strict";

  const SETTLE_MS = 1400;
  // Below this a fragment is noise ("uh", "so") — and on its own not a line
  // anyone asks a question in.
  const MIN_CHARS = 2;

  class CaptionTracker {
    constructor({ settleMs = SETTLE_MS } = {}) {
      this.settleMs = settleMs;
      this.blocks = new Map(); // block id -> {speaker, text, changedAt, emitted}
    }

    /** One observation of one block. Returns nothing; call flush() to collect. */
    observe(id, speaker, text, now) {
      const clean = normalise(text);
      const b = this.blocks.get(id);
      if (!b) {
        this.blocks.set(id, { speaker, text: clean, changedAt: now, emitted: "" });
        return;
      }
      if (clean !== b.text) {
        b.text = clean;
        b.changedAt = now;
      }
      if (speaker) b.speaker = speaker;
    }

    /** A block left the DOM: whatever it had is final now. */
    remove(id, now) {
      const b = this.blocks.get(id);
      if (!b) return [];
      b.changedAt = -Infinity;
      const out = this.flush(now, [id]);
      this.blocks.delete(id);
      return out;
    }

    /** Settled lines since the last flush, oldest block first. */
    flush(now, only) {
      const out = [];
      for (const [id, b] of this.blocks) {
        if (only && !only.includes(id)) continue;
        if (now - b.changedAt < this.settleMs) continue;
        const fresh = unsent(b.emitted, b.text);
        if (fresh.length >= MIN_CHARS) {
          out.push({ speaker: b.speaker || "Someone", text: fresh });
        }
        b.emitted = b.text;
      }
      return out;
    }
  }

  function normalise(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
  }

  /** The part of `now` not already sent from this block.
   *
   * Usually `now` extends what was sent and the answer is the tail. When the
   * recogniser rewrote words we had ALREADY sent, there is no taking them
   * back — so skip as many words as were sent and send only what follows,
   * rather than re-sending the whole block as if it were new.
   */
  function unsent(sent, now) {
    if (!sent) return now;
    if (now.startsWith(sent)) return now.slice(sent.length).trim();
    const already = sent.split(" ").length;
    return now.split(" ").slice(already).join(" ").trim();
  }

  // ── DOM ─────────────────────────────────────────────────────────────────
  // Meet's markup changes without notice, so extraction tries layered
  // strategies, most specific first, and reports which one matched (the
  // panel shows it) so a silent breakage is visible rather than quiet.

  const STRATEGIES = [
    { name: "jsname", region: '[jsname="tgaKEf"], [jsname="DS9Ooe"]',
      block: '[jsname="YSxPC"]', speaker: '[jsname="r4nke"]', text: '[jsname="bVV8Bd"]' },
    { name: "classes", region: '[jsname="dsyhDe"], [role="region"][aria-label*="aption" i]',
      block: ".nMcdL", speaker: ".NWpY1d", text: ".ygicle" },
    { name: "structure", region: '[role="region"][aria-label*="aption" i], [role="region"][aria-label*="subtitle" i]',
      block: null, speaker: null, text: null },
  ];

  function readCaptionBlocks(doc) {
    for (const s of STRATEGIES) {
      const region = doc.querySelector(s.region);
      if (!region) continue;
      const blocks = s.block ? bySelectors(region, s) : byStructure(region);
      if (blocks.length || s.block === null) return { strategy: s.name, region: true, blocks };
    }
    return { strategy: null, region: false, blocks: [] };
  }

  function bySelectors(region, s) {
    const out = [];
    for (const el of region.querySelectorAll(s.block)) {
      const speaker = el.querySelector(s.speaker);
      const text = el.querySelector(s.text);
      if (text && normalise(text.textContent)) {
        out.push({ el, speaker: normalise(speaker && speaker.textContent), text: text.textContent });
      }
    }
    return out;
  }

  /** Each child block: first text-bearing child is the speaker, the rest the
   *  words — Meet has always put the name above the caption. */
  function byStructure(region) {
    const out = [];
    for (const el of region.children) {
      const kids = Array.from(el.children).filter((k) => normalise(k.textContent));
      if (kids.length < 2) continue;
      const speaker = normalise(kids[0].textContent);
      const text = kids.slice(1).map((k) => k.textContent).join(" ");
      if (speaker.length <= 60) out.push({ el, speaker, text });
    }
    return out;
  }

  // How Meet labels the local user's own captions. Their own lines are never
  // questions to answer — sent as "ours" rather than resolved by name.
  const SELF_LABELS = new Set(["you", "du", "vous", "tú", "tu", "voi", "jij", "você", "आप"]);
  const isSelf = (speaker) => SELF_LABELS.has(normalise(speaker).toLowerCase());

  const api = { CaptionTracker, readCaptionBlocks, isSelf, unsent, SETTLE_MS };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PhotonCaptions = api;
})(typeof self !== "undefined" ? self : this);
