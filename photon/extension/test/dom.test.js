// readCaptionBlocks against Meet-shaped markup, one fixture per strategy.
//
// No DOM library is installed for this folder, so a tiny element model below
// implements exactly the selector forms captions.js uses: [a="v"], [a*="v" i],
// .class, compounds of those, and comma lists. Meet's real DOM still has to be
// checked in a live call — this guards the extraction logic, not Google's markup.
const test = require("node:test");
const assert = require("node:assert/strict");
const { readCaptionBlocks } = require("../captions.js");

class El {
  constructor(attrs = {}, children = [], text = "") {
    this.attrs = attrs;
    this.children = children;
    this.ownText = text;
  }
  get textContent() {
    return this.ownText + this.children.map((c) => c.textContent).join("");
  }
  matches(sel) {
    return sel.split(",").some((one) => matchCompound(this, one.trim()));
  }
  *walk() {
    for (const c of this.children) {
      yield c;
      yield* c.walk();
    }
  }
  querySelectorAll(sel) {
    return [...this.walk()].filter((e) => e.matches(sel));
  }
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }
}

function matchCompound(el, sel) {
  const parts = sel.match(/\[[^\]]+\]|\.[\w-]+/g) || [];
  return parts.every((p) => {
    if (p.startsWith(".")) return (el.attrs.class || "").split(" ").includes(p.slice(1));
    const m = p.match(/^\[([\w-]+)(\*?=)"([^"]*)"( i)?\]$/);
    const v = el.attrs[m[1]];
    if (v == null) return false;
    if (m[2] === "=") return v === m[3];
    return m[4] ? v.toLowerCase().includes(m[3].toLowerCase()) : v.includes(m[3]);
  });
}

const doc = (...children) => new El({}, children);

test("jsname markup", () => {
  const d = doc(new El({ jsname: "tgaKEf" }, [
    new El({ jsname: "YSxPC" }, [
      new El({ jsname: "r4nke" }, [], "Dana Whitfield"),
      new El({ jsname: "bVV8Bd" }, [], "does the export support date filters?"),
    ]),
    new El({ jsname: "YSxPC" }, [
      new El({ jsname: "r4nke" }, [], "You"),
      new El({ jsname: "bVV8Bd" }, [], "good question"),
    ]),
  ]));
  const r = readCaptionBlocks(d);
  assert.equal(r.strategy, "jsname");
  assert.deepEqual(r.blocks.map((b) => [b.speaker, b.text]), [
    ["Dana Whitfield", "does the export support date filters?"],
    ["You", "good question"],
  ]);
});

test("class markup when the jsnames change", () => {
  const d = doc(new El({ role: "region", "aria-label": "Captions" }, [
    new El({ class: "nMcdL bj4p3b" }, [
      new El({ class: "NWpY1d" }, [], "Dana"),
      new El({ class: "ygicle VbkSUe" }, [], "is Okta supported?"),
    ]),
  ]));
  const r = readCaptionBlocks(d);
  assert.equal(r.strategy, "classes");
  assert.deepEqual(r.blocks.map((b) => [b.speaker, b.text]), [["Dana", "is Okta supported?"]]);
});

test("structure only: the labelled region, name above words", () => {
  const d = doc(new El({ role: "region", "aria-label": "Captions" }, [
    new El({}, [new El({}, [], "Dana"), new El({}, [], "what about SSO?")]),
    new El({}, [new El({}, [], "a lone status line")]), // not a caption block
  ]));
  const r = readCaptionBlocks(d);
  assert.equal(r.strategy, "structure");
  assert.deepEqual(r.blocks.map((b) => [b.speaker, b.text]), [["Dana", "what about SSO?"]]);
});

test("captions off: no region, so the panel can say so", () => {
  const r = readCaptionBlocks(doc(new El({ role: "region", "aria-label": "People" })));
  assert.deepEqual(r, { strategy: null, region: false, blocks: [] });
});

test("an empty captions region is still 'captions on'", () => {
  const r = readCaptionBlocks(doc(new El({ role: "region", "aria-label": "Captions" })));
  assert.equal(r.region, true);
  assert.deepEqual(r.blocks, []);
});
