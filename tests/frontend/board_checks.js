// Drive components/trade_board/frontend/index.html in jsdom: feed it a
// render message, then synthesise the pointer gestures a trader makes.
//
// This is the only test of the board's interior. AppTest never renders a
// custom component's iframe, so dragging, hit-testing and the rebuild guard
// are invisible to the Python suite — and the rebuild guard in particular
// is the thing that, if it broke, would snap every square back to its
// auto-layout mid-drag. Run via tests/ui/test_board_frontend.py, or
// directly with `node tests/frontend/board_checks.js`.
//
// Exits non-zero if any check fails.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const HTML = fs.readFileSync(
  path.resolve(__dirname, "../../components/trade_board/frontend/index.html"),
  "utf8"
);

const sent = [];
// Everything the page's own script touches on load has to exist before it
// parses, so the stubs go in via beforeParse.
const dom = new JSDOM(HTML, {
  runScripts: "dangerously",
  pretendToBeVisual: true,
  beforeParse(win) {
    Object.defineProperty(win, "parent", {
      value: { postMessage: (m) => sent.push(m) },
      configurable: true,
    });
    Object.defineProperty(win.HTMLElement.prototype, "clientWidth", { get() { return 1200; }, configurable: true });
    Object.defineProperty(win.HTMLElement.prototype, "clientHeight", { get() { return 520; }, configurable: true });
    win.HTMLElement.prototype.getBoundingClientRect = function () {
      return { left: 0, top: 0, right: 1200, bottom: 520, width: 1200, height: 240 };
    };
    if (!win.CSS) win.CSS = {};
    win.CSS.escape = (s) => String(s).replace(/[^\w-]/g, (c) => "\\" + c);
    // jsdom has no hit-testing; tests point this at the intended drop target.
    win.document.elementFromPoint = () => null;
  },
});
const win = dom.window;

// Dispatched directly rather than via postMessage: jsdom delivers that
// asynchronously, and these checks read the DOM straight after.
function render(args) {
  win.dispatchEvent(
    new win.MessageEvent("message", {
      data: { type: "streamlit:render", args, theme: { base: "light" } },
    })
  );
}
function pointer(type, x, y, target) {
  const ev = new win.MouseEvent(type, { clientX: x, clientY: y, bubbles: true, cancelable: true });
  (target || win.document).dispatchEvent(ev);
}
function lastValue() {
  for (let i = sent.length - 1; i >= 0; i--) {
    if (sent[i].type === "streamlit:setComponentValue") return sent[i].value;
  }
  return null;
}
function check(name, cond, extra) {
  console.log((cond ? "PASS  " : "FAIL  ") + name + (cond ? "" : "   <- " + JSON.stringify(extra)));
  if (!cond) process.exitCode = 1;
}

const squares = [
  { key: "db:1", side: "buy",  name: "AZPS", detail: "PALOVERDE500 · 7-22 · 1,600", matched_frac: 0,   is_market: false, related: true, title: "buy" },
  { key: "db:2", side: "sell", name: "BPAT", detail: "MIDC · 7-22 · 960",           matched_frac: 0.5, is_market: false, related: true, title: "sell" },
  { key: "db:3", side: "buy",  name: "PNM",  detail: "MEAD230 · 1-24 · 600",        matched_frac: 1,   is_market: false, related: true, title: "buy2" },
];
const markets = [{ name: "CAISO", mwh: "" }, { name: "SWPW", mwh: "1,600" }, { name: "AESO", mwh: "" }];
const links = [{ link_id: "L1", buy_key: "db:1", sell_key: "db:2", buy_market: null, sell_market: null, mwh: 960, label: "L1", related: true }];

// --- first render -----------------------------------------------------
render({ squares, links, markets, positions: {}, selected: null, revision: 1, height: 240 });

const doc = win.document;
const board = doc.getElementById("board");
check("ready message sent", sent.some((m) => m.type === "streamlit:componentReady"));
check("frame height sent", sent.some((m) => m.type === "streamlit:setFrameHeight"));
check("one node per square", doc.querySelectorAll(".sq").length === 3, doc.querySelectorAll(".sq").length);
check("one chip per market", doc.querySelectorAll(".chip").length === 3);
check("used chip is marked", doc.querySelector('[data-market="SWPW"]').classList.contains("used"));
check("link drawn as two paths", doc.querySelectorAll("#wires path").length === 2, doc.querySelectorAll("#wires path").length);
check("fully matched square flagged", doc.querySelector('[data-key="db:3"]').classList.contains("full"));

// Auto-layout: buys left, sells right, middle clear.
const pos = (k) => {
  const e = doc.querySelector(`[data-key="${k}"]`);
  return [parseFloat(e.style.left), parseFloat(e.style.top)];
};
check("buys laid out on the left", pos("db:1")[0] < 300, pos("db:1"));
check("sells laid out on the right", pos("db:2")[0] > 900, pos("db:2"));
check("buys stack vertically", pos("db:3")[1] > pos("db:1")[1], [pos("db:1"), pos("db:3")]);

// --- dragging a square ------------------------------------------------
const sq1 = doc.querySelector('[data-key="db:1"]');
pointer("pointerdown", 30, 30, sq1);
pointer("pointermove", 430, 230);
pointer("pointerup", 430, 230);
let v = lastValue();
check("drag emits a move event", v && v.type === "move", v);
check("move carries the new position", v && v.positions["db:1"][0] > 300, v && v.positions["db:1"]);
check("square actually moved in the DOM", pos("db:1")[0] > 300, pos("db:1"));

// --- click (a drag under the threshold) -------------------------------
pointer("pointerdown", 30, 30, doc.querySelector('[data-key="db:2"]'));
pointer("pointerup", 31, 30);
v = lastValue();
check("a click emits select", v && v.type === "select" && v.key === "db:2", v);

// --- drag the + grip onto the other side ------------------------------
const grip = doc.querySelector('[data-key="db:1"] .grip');
pointer("pointerdown", 100, 100, grip);
check("rubber band appears", doc.querySelectorAll("#wires path").length === 3);
check("wires stop intercepting during a link drag", board.classList.contains("linking"));
pointer("pointermove", 500, 200);
// elementFromPoint is what decides the drop; point it at the sell square.
doc.elementFromPoint = () => doc.querySelector('[data-key="db:2"]');
pointer("pointerup", 500, 200);
v = lastValue();
check("grip drag emits link_request", v && v.type === "link_request", v);
check("link_request names both ends", v && v.from === "db:1" && v.to === "db:2", v);
check("rubber band cleaned up", doc.querySelectorAll("#wires path").length === 2);
check("linking class cleared", !board.classList.contains("linking"));

// --- a link onto a market chip ----------------------------------------
pointer("pointerdown", 100, 100, doc.querySelector('[data-key="db:1"] .grip'));
doc.elementFromPoint = () => doc.querySelector('[data-market="CAISO"]');
pointer("pointerup", 600, 500);
v = lastValue();
check("market drop emits link_request with a market", v && v.market === "CAISO", v);

// --- same-side drop is refused ----------------------------------------
const before = lastValue().seq;
pointer("pointerdown", 100, 100, doc.querySelector('[data-key="db:1"] .grip'));
doc.elementFromPoint = () => doc.querySelector('[data-key="db:3"]');  // also a buy
pointer("pointerup", 200, 300);
check("buy->buy drop emits nothing", lastValue().seq === before, lastValue());

// --- clicking a link --------------------------------------------------
doc.elementFromPoint = () => null;
const hit = doc.querySelectorAll("#wires path")[1];
hit.dispatchEvent(new win.MouseEvent("click", { bubbles: true }));
v = lastValue();
check("clicking a wire emits link_click", v && v.type === "link_click" && v.link_id === "L1", v);

// --- positions survive a rebuild --------------------------------------
// Focused on db:1, which is linked to db:2 — so db:3 is the odd one out and
// Python marks it related:false.
const focusedSquares = squares.map((s) => ({ ...s, related: s.key !== "db:3" }));
const moved = pos("db:1");
render({ squares: focusedSquares, links, markets, positions: { "db:1": moved }, selected: "db:1", revision: 2, height: 240 });
check("a rebuild honours stored positions", Math.abs(pos("db:1")[0] - moved[0]) < 1, [pos("db:1"), moved]);
check("unrelated squares dim around the focus", doc.querySelector('[data-key="db:3"]').classList.contains("dim"));
check("the focused square is not dimmed", !doc.querySelector('[data-key="db:1"]').classList.contains("dim"));
check("a linked square stays lit", !doc.querySelector('[data-key="db:2"]').classList.contains("dim"));

// --- same revision must NOT rebuild (or a drag snaps back) ------------
const beforeCount = doc.querySelectorAll(".sq").length;
doc.querySelector('[data-key="db:1"]').dataset.marker = "kept";
render({ squares: focusedSquares, links, markets, positions: {}, selected: "db:1", revision: 2, height: 240 });
check("an identical revision does not rebuild",
      doc.querySelector('[data-key="db:1"]').dataset.marker === "kept");
check("square count unchanged", doc.querySelectorAll(".sq").length === beforeCount);

// --- clearing a square off the board ----------------------------------
render({ squares, links, markets, positions: {}, selected: null, revision: 10, height: 240 });
pointer("pointerdown", 20, 20, doc.querySelector('[data-key="db:2"] .kill'));
v = lastValue();
check("the x emits dismiss", v && v.type === "dismiss" && v.key === "db:2", v);
check("the x does not also start a drag", !doc.querySelector('[data-key="db:2"]').classList.contains("dragging"));
pointer("pointerup", 20, 20);

// --- a new trade lands at the bottom of its side, not on top -----------
// db:1 and db:3 are buys; pin them to slots 0 and 1, then add a third.
render({ squares, links, markets, positions: {}, selected: null, revision: 11, height: 240 });
const buy1 = pos("db:1"), buy3 = pos("db:3");
const withNew = squares.concat([
  { key: "db:4", side: "buy", name: "NEW", detail: "x · 1-24 · 240", matched_frac: 0, is_market: false, related: true, title: "new" },
]);
render({ squares: withNew, links, markets,
         positions: { "db:1": buy1, "db:3": buy3 }, selected: null, revision: 12, height: 240 });
const newPos = pos("db:4");
const clash = [buy1, buy3].some((p) => Math.abs(p[0] - newPos[0]) < 150 && Math.abs(p[1] - newPos[1]) < 46);
check("a new square does not land on an existing one", !clash, { newPos, buy1, buy3 });
check("a new square lands below the ones already there", newPos[1] > Math.max(buy1[1], buy3[1]), { newPos, buy1, buy3 });
check("a new buy stays on the buy side", newPos[0] < 300, newPos);

// --- a plain click on a chip opens its bid panel, not a link ----------
// (this is the actual feature: a chip is both a drag source for links and
// a click target for the SWPW bid-file builder — the two have to be told
// apart by movement, exactly like a square's click-vs-drag already is.)
render({ squares, links, markets, positions: {}, selected: null, revision: 13, height: 240 });
pointer("pointerdown", 600, 500, doc.querySelector('[data-market="CAISO"]'));
check("a chip press does not start linking immediately", !board.classList.contains("linking"));
pointer("pointerup", 600, 500);
v = lastValue();
check("a chip click emits chip_click", v && v.type === "chip_click" && v.market === "CAISO", v);
check("a chip click does not start a link", !board.classList.contains("linking"));

// --- a link started from a market chip --------------------------------
pointer("pointerdown", 600, 500, doc.querySelector('[data-market="CAISO"]'));
pointer("pointermove", 620, 500);  // past the click/drag threshold
check("dragging from a chip starts a link", board.classList.contains("linking"));
doc.elementFromPoint = () => doc.querySelector('[data-key="db:2"]');
pointer("pointermove", 900, 100);
pointer("pointerup", 900, 100);
v = lastValue();
check("a chip-originated drop names the market as the source",
      v && v.type === "link_request" && v.from_market === "CAISO" && v.to === "db:2", v);

// A market can't be linked to itself.
pointer("pointerdown", 600, 500, doc.querySelector('[data-market="CAISO"]'));
pointer("pointermove", 620, 500);
const beforeChip = lastValue().seq;
doc.elementFromPoint = () => doc.querySelector('[data-market="AESO"]');
pointer("pointerup", 700, 500);
check("chip -> chip emits nothing", lastValue().seq === beforeChip, lastValue());

// --- an abandoned gesture must not poison the next one ----------------
// Releasing outside the iframe never delivers pointerup here, so a gesture
// can be left hanging. pointerup checks chipDown before linking, so a
// stale chip press used to hijack the next link drag: it emitted
// chip_click and swallowed the link entirely.
render({ squares, links, markets, positions: {}, selected: null, revision: 14, height: 240 });
pointer("pointerdown", 600, 500, doc.querySelector('[data-market="CAISO"]'));  // no pointerup: abandoned
const beforeStale = lastValue() ? lastValue().seq : 0;
pointer("pointerdown", 100, 100, doc.querySelector('[data-key="db:1"] .grip'));
pointer("pointermove", 500, 200);
doc.elementFromPoint = () => doc.querySelector('[data-key="db:2"]');
pointer("pointerup", 500, 200);
v = lastValue();
check("an abandoned chip press does not hijack the next link",
      v && v.type === "link_request" && v.from === "db:1" && v.to === "db:2", v);
check("...and emits no stray chip_click", v.seq === beforeStale + 1, {seq: v.seq, beforeStale});

// The same the other way round: an abandoned link drag must not block a
// later chip click.
pointer("pointerdown", 100, 100, doc.querySelector('[data-key="db:1"] .grip'));
pointer("pointermove", 400, 300);   // linking now in flight, then abandoned
pointer("pointerdown", 600, 500, doc.querySelector('[data-market="AESO"]'));
pointer("pointerup", 600, 500);
v = lastValue();
check("an abandoned link drag does not block a later chip click",
      v && v.type === "chip_click" && v.market === "AESO", v);
check("the abandoned rubber band is cleaned up", doc.querySelectorAll("#wires path").length === 2,
      doc.querySelectorAll("#wires path").length);
check("the linking class is not left stuck on", !board.classList.contains("linking"));

// --- every event carries the instance id Python dedupes against -------
check("events carry a per-frame instance id", typeof v.instance === "string" && v.instance.length > 0, v.instance);

// --- the rail's hint must not sit on top of the chips ------------------
// It used to be position:absolute, which painted it over the chips and made
// elementFromPoint return the hint — so every drop aimed at a market chip
// silently did nothing.
const hintEl = doc.getElementById("hint");
const hintStyle = win.getComputedStyle(hintEl);
check("the hint is in flow, not overlaying the rail", hintStyle.position !== "absolute", hintStyle.position);
check("the hint never takes a pointer event", hintStyle.pointerEvents === "none", hintStyle.pointerEvents);
const railEl = doc.getElementById("rail");
check("the chip row comes after the hint in the rail",
      Array.prototype.indexOf.call(railEl.children, doc.getElementById("chipRow")) >
      Array.prototype.indexOf.call(railEl.children, hintEl));

// --- market chips are centered and well spaced, not right-packed -------
// (this is the actual bug reported: the hint used to grow and push the
// chips to the right edge instead of centering them.)
const chipRowStyle = win.getComputedStyle(doc.getElementById("chipRow"));
check("the chip row does not stretch to fill the rail",
      chipRowStyle.flexGrow === "0", chipRowStyle.flexGrow);
// jsdom has no real layout engine (offsetLeft/offsetWidth are always 0
// here), so the actual pixel gap between chips can't be measured this way
// — this checks the authored rule that produces it in a real browser.
check("chips are given real breathing room, not packed tight",
      parseFloat(chipRowStyle.gap) >= 20, chipRowStyle.gap);

// --- the + grip must not be clipped by the square ---------------------
check("squares do not clip their grip",
      win.getComputedStyle(doc.querySelector(".sq")).overflow !== "hidden",
      win.getComputedStyle(doc.querySelector(".sq")).overflow);

// --- the x is visible without hovering --------------------------------
const killStyle = win.getComputedStyle(doc.querySelector(".kill"));
check("the x is visible at rest", parseFloat(killStyle.opacity) > 0, killStyle.opacity);

// --- the board fills the window ---------------------------------------
// frameElement/parent are absent in this harness, so the fallback applies.
// --- the canvas sizes itself to the day --------------------------------
// Three buys and one sell: tall enough for three, plus two squares' worth
// of clear space above the market rail so the links into it stay readable.
const SQ_H = 46, GAP = 7, PAD = 20, RAIL_H = 30, CLEAR_ROWS = 2, FLOOR = 240;
function expectedHeight(rows) {
  const stack = rows ? rows * (SQ_H + GAP) - GAP : 0;
  return Math.max(FLOOR, PAD + stack + CLEAR_ROWS * (SQ_H + GAP) + RAIL_H);
}
render({ squares, links, markets, positions: {}, selected: null, revision: 20, height: 240 });
let heights = sent.filter((m) => m.type === "streamlit:setFrameHeight").map((m) => m.height);
check("the canvas is sized for the busier side", heights[heights.length - 1] === expectedHeight(2) + 4,
      [heights[heights.length - 1], expectedHeight(2) + 4]);

const quiet = heights[heights.length - 1];
const many = [];
for (let i = 0; i < 8; i++) many.push({ ...squares[0], key: "db:m" + i });
render({ squares: many, links: [], markets, positions: {}, selected: null, revision: 22, height: 240 });
heights = sent.filter((m) => m.type === "streamlit:setFrameHeight").map((m) => m.height);
check("a busy day grows instead of stacking a second column",
      heights[heights.length - 1] === expectedHeight(8) + 4,
      [heights[heights.length - 1], expectedHeight(8) + 4]);
check("so a quiet day's canvas really is the shorter one",
      quiet < heights[heights.length - 1], [quiet, heights[heights.length - 1]]);
check("and every square is in one column", new Set(
        many.map((sq) => doc.querySelector('[data-key="' + sq.key + '"]').style.left)).size === 1);

render({ squares: [], links: [], markets, positions: {}, selected: null, revision: 23, height: 240 });
heights = sent.filter((m) => m.type === "streamlit:setFrameHeight").map((m) => m.height);
check("an empty board still has room to drop something into",
      heights[heights.length - 1] === 244, heights[heights.length - 1]);

// --- empty board -------------------------------------------------------
render({ squares: [], links: [], markets, positions: {}, selected: null, revision: 3, height: 240 });
check("empty board says so", !!doc.getElementById("empty"));
render({ squares, links, markets, positions: {}, selected: null, revision: 4, height: 240 });
check("empty placeholder removed on rebuild", doc.querySelectorAll("#empty").length === 0,
      doc.querySelectorAll("#empty").length);

console.log(process.exitCode ? "\nSOME CHECKS FAILED" : "\nALL CHECKS PASSED");
