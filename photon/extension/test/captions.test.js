// node --test extension/test
// The caption tracker is the part of the extension that decides what the
// client "said" — tested here with no browser, on the shapes Meet produces.
const test = require("node:test");
const assert = require("node:assert/strict");
const { CaptionTracker, isSelf, unsent } = require("../captions.js");

test("a growing block is sent once, when it settles", () => {
  const t = new CaptionTracker({ settleMs: 1000 });
  t.observe("b1", "Dana", "does the", 0);
  t.observe("b1", "Dana", "does the export", 300);
  t.observe("b1", "Dana", "does the export support date filters?", 600);
  assert.deepEqual(t.flush(1000), []); // still changing 400ms ago
  assert.deepEqual(t.flush(1700), [{ speaker: "Dana", text: "does the export support date filters?" }]);
  assert.deepEqual(t.flush(5000), []); // nothing new, nothing sent
});

test("a block that keeps going sends only what is new", () => {
  const t = new CaptionTracker({ settleMs: 500 });
  t.observe("b1", "Dana", "we use SSO.", 0);
  assert.equal(t.flush(600)[0].text, "we use SSO.");
  t.observe("b1", "Dana", "we use SSO. Is Okta supported?", 700);
  assert.deepEqual(t.flush(1300), [{ speaker: "Dana", text: "Is Okta supported?" }]);
});

test("a rewrite of words already sent is not re-sent", () => {
  assert.equal(unsent("does the export", "do the exports time out"), "time out");
  assert.equal(unsent("does the export", "does the export"), "");
  assert.equal(unsent("", "hello"), "hello");
});

test("a block leaving the DOM is final immediately", () => {
  const t = new CaptionTracker({ settleMs: 5000 });
  t.observe("b1", "Dana", "one more thing", 0);
  assert.deepEqual(t.remove("b1", 10), [{ speaker: "Dana", text: "one more thing" }]);
  assert.deepEqual(t.flush(99999), []);
});

test("blocks are tracked per speaker turn", () => {
  const t = new CaptionTracker({ settleMs: 100 });
  t.observe("b1", "Dana", "can you send the docs?", 0);
  t.observe("b2", "You", "sure, I'll send them", 0);
  assert.deepEqual(t.flush(200).map((l) => l.speaker), ["Dana", "You"]);
});

test("noise and whitespace", () => {
  const t = new CaptionTracker({ settleMs: 100 });
  t.observe("b1", "Dana", "  a  ", 0);
  assert.deepEqual(t.flush(200), []);
  t.observe("b2", "", "what   about\n exports", 0);
  assert.deepEqual(t.flush(400), [{ speaker: "Someone", text: "what about exports" }]);
});

test("the local user's own label", () => {
  assert.ok(isSelf("You"));
  assert.ok(isSelf(" you "));
  assert.ok(!isSelf("Yousef"));
});
