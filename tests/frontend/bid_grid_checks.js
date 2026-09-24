// Drive components/bid_grid/frontend/index.html in jsdom: feed it a render
// message, then make the edits a trader makes.
//
// This is the only test of the grid's interior. AppTest never renders a
// custom component's iframe, so the three-level header, the blank-vs-zero
// rule, and the patch-in-place that keeps the caret where it was are all
// invisible to the Python suite. Run via tests/ui/test_bidgrid_frontend.py,
// or directly with `node tests/frontend/bid_grid_checks.js`.
//
// Exits non-zero if any check fails.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const HTML = fs.readFileSync(
  path.resolve(__dirname, "../../components/bid_grid/frontend/index.html"),
  "utf8"
);

const sent = [];
const dom = new JSDOM(HTML, {
  runScripts: "dangerously",
  pretendToBeVisual: true,
  // Everything the page's script touches on load has to exist before it
  // parses, so the stub goes in via beforeParse.
  beforeParse(win) {
    Object.defineProperty(win, "parent", {
      value: { postMessage: (m) => sent.push(m) },
      configurable: true,
    });
  },
});
const win = dom.window;
const doc = win.document;

// Dispatched directly rather than via postMessage: jsdom delivers that
// asynchronously, and these checks read the DOM straight after.
function render(args) {
  win.dispatchEvent(
    new win.MessageEvent("message", {
      data: { type: "streamlit:render", args, theme: { base: "light" } },
    })
  );
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
function cell(id) {
  return doc.querySelector('input[data-id="' + id + '"]');
}
function type(id, value) {
  const input = cell(id);
  input.value = value;
  input.dispatchEvent(new win.Event("change", { bubbles: true }));
  return input;
}

// --- the payload -------------------------------------------------------
const HOURS = [];
for (let h = 1; h <= 24; h++) {
  const e = h + 3;
  HOURS.push({ he: h, ept: e <= 24 ? String(e) : e - 24 + "*" });
}
function col(value, from, to) {
  const out = Array(24).fill(null);
  for (let h = from; h <= to; h++) out[h - 1] = value;
  return out;
}
function line(code, mw, price, total) {
  return { code: code, mw: mw, price: price, total: total };
}
function payload(azpsLines, bad) {
  return {
    hours: HOURS,
    sides: [
      {
        side: "SHORT", sub: "Source > market", code_label: "GCA",
        groups: [{ pse: "AZPS", total: 1600, bad_hours: bad || {}, lines: azpsLines }],
      },
      {
        side: "LONG", sub: "market > Sink", code_label: "LCA",
        groups: [{
          pse: "BPAT", total: 960, bad_hours: {},
          lines: [line("MIDC", col(60, 7, 22), col(50, 7, 22), 960)],
        }],
      },
    ],
  };
}

const whole = [line("", col(100, 7, 22), col(0, 7, 22), 1600)];
render(Object.assign(payload(whole), { revision: 1 }));

// --- structure ---------------------------------------------------------
check("one panel per side", doc.querySelectorAll(".panel").length === 2);
check("the sides are side by side, not stacked",
      win.getComputedStyle(doc.getElementById("wrap")).display === "flex");
check("three header levels", doc.querySelectorAll("thead tr").length === 6,
      doc.querySelectorAll("thead tr").length);   // 3 per panel

const shortPanel = doc.querySelector('.panel[data-side="SHORT"]');
const level1 = shortPanel.querySelectorAll("thead tr:first-child th");
check("level 1 is the counterparty", level1[2].textContent === "AZPS", level1[2].textContent);
check("it spans its whole pair", level1[2].colSpan === 2, level1[2].colSpan);
check("level 2 is the code, spanning the pair",
      shortPanel.querySelector("th.code").colSpan === 2);
check("level 3 is MW and Price",
      Array.from(shortPanel.querySelectorAll("thead tr:last-child th")).map((t) => t.textContent)
        .join(",") === "MW,Price",
      Array.from(shortPanel.querySelectorAll("thead tr:last-child th")).map((t) => t.textContent));
check("the index columns are fixed-width, so an input can't blow them out",
      shortPanel.querySelectorAll("colgroup col.ixc").length === 2);
check("one data column per MW and per price",
      shortPanel.querySelectorAll("colgroup col.datac").length === 2);

// --- the index ---------------------------------------------------------
const rows = shortPanel.querySelectorAll("tbody tr");
check("24 hour rows", rows.length === 24, rows.length);
check("HE is the first index column", rows[6].children[0].textContent === "7");
check("EPT beside it: PPT HE7 is EPT HE10", rows[6].children[1].textContent === "10");
check("and the last three PPT hours are the file's starred rows",
      rows[23].children[1].textContent === "3*", rows[23].children[1].textContent);

// --- blank vs zero -----------------------------------------------------
check("an hour with MW shows it", cell("SHORT|AZPS|0|mw|7").value === "100");
check("an hour without is blank, as in the file", cell("SHORT|AZPS|0|mw|1").value === "",
      cell("SHORT|AZPS|0|mw|1").value);
check("a price of 0 is shown, not blanked", cell("SHORT|AZPS|0|price|7").value === "0",
      cell("SHORT|AZPS|0|price|7").value);
check("but no price where there's no MW", cell("SHORT|AZPS|0|price|1").value === "");
check("the long side's own default came through", cell("LONG|BPAT|0|price|7").value === "50");
check("the footer totals the line", shortPanel.querySelector('[data-id="SHORT|AZPS|0|total"]')
      .textContent === "1600");

// --- edits -------------------------------------------------------------
type("SHORT|AZPS|0|mw|7", "60");
check("a MW edit reports the cell it happened in",
      JSON.stringify(lastValue()) ===
      JSON.stringify({ type: "mw", side: "SHORT", pse: "AZPS", line: 0, hour: 7, value: 60,
                       seq: lastValue().seq, instance: lastValue().instance }),
      lastValue());

type("SHORT|AZPS|0|price|11", "-1");
check("a price edit carries no hour — it's one price for the line",
      lastValue().type === "price" && lastValue().value === -1 && lastValue().hour === undefined,
      lastValue());

type("SHORT|AZPS|0|price|11", "");
check("a cleared price is null, not zero", lastValue().value === null, lastValue());

type("SHORT|AZPS|0|code", "paloverde");
check("a code is upper-cased on the way out, matching what the cell shows",
      lastValue().type === "code" && lastValue().value === "PALOVERDE", lastValue());

check("every event carries a seq and a frame instance",
      lastValue().seq > 0 && typeof lastValue().instance === "string", lastValue());

// Enter commits and walks down the column — entering a shape hour by hour
// is the one thing done repeatedly here.
const before = cell("SHORT|AZPS|0|mw|8");
before.focus();
before.dispatchEvent(new win.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
check("Enter moves to the same column's next hour",
      doc.activeElement === cell("SHORT|AZPS|0|mw|9"),
      doc.activeElement && doc.activeElement.dataset.id);

// --- the + and × on a code cell ----------------------------------------
shortPanel.querySelector('.add[data-pse="AZPS"]').dispatchEvent(
  new win.MouseEvent("click", { bubbles: true })
);
check("+ on a code cell asks for a split",
      lastValue().type === "split" && lastValue().pse === "AZPS" && lastValue().line === 0,
      lastValue());
check("a first line has no × — its MW has nowhere to go back to",
      shortPanel.querySelectorAll(".del").length === 0);

// --- rebuild on a structure change --------------------------------------
const split = [
  line("PALOVERDE", col(60, 7, 22), col(0, 7, 22), 960),
  line("", col(40, 7, 22), col(0, 7, 22), 640),
];
render(Object.assign(payload(split), { revision: 2 }));
check("the split's pair appeared", !!cell("SHORT|AZPS|1|mw|7"));
check("level 1 now spans both pairs",
      doc.querySelector('.panel[data-side="SHORT"] thead tr:first-child th:nth-child(3)').colSpan === 4);
check("and the split can be taken back",
      doc.querySelectorAll('.panel[data-side="SHORT"] .del').length === 1);
check("the caret went to the new code box, ready to type",
      doc.activeElement === cell("SHORT|AZPS|1|code"),
      doc.activeElement && doc.activeElement.dataset.id);

// --- patch in place ------------------------------------------------------
const node = cell("SHORT|AZPS|0|mw|7");
const rebalanced = [
  line("PALOVERDE", col(30, 7, 22), col(0, 7, 22), 480),
  line("MEAD230", col(70, 7, 22), col(0, 7, 22), 1120),
];
render(Object.assign(payload(rebalanced), { revision: 3 }));
check("a value-only change patches rather than rebuilds",
      cell("SHORT|AZPS|0|mw|7") === node);
check("the patched value is there", node.value === "30", node.value);
check("the totals follow", doc.querySelector('[data-id="SHORT|AZPS|1|total"]').textContent === "1120");

// The round trip through Python must not yank a half-typed cell away.
const typing = cell("SHORT|AZPS|1|mw|9");
typing.focus();
typing.value = "12";
render(Object.assign(payload(rebalanced), { revision: 4 }));
check("a cell being typed into is left alone", typing.value === "12", typing.value);
check("and keeps the caret", doc.activeElement === typing);

// --- a mismatch is shown where it happened -------------------------------
render(Object.assign(payload(rebalanced, { 9: "Splits total 150 MW here; the schedule calls for 100 MW." }),
                     { revision: 5 }));
const badCells = doc.querySelectorAll("td.bad");
check("the hour that doesn't reconcile is tinted", badCells.length === 2, badCells.length);
check("and says what's wrong with it",
      badCells[0].title.indexOf("150 MW") >= 0, badCells[0].title);
render(Object.assign(payload(rebalanced), { revision: 6 }));
check("and the tint clears once it does", doc.querySelectorAll("td.bad").length === 0);

// --- housekeeping ---------------------------------------------------------
check("ready message sent", sent.some((m) => m.type === "streamlit:componentReady"));
check("frame height reported", sent.some((m) => m.type === "streamlit:setFrameHeight"));

render({ hours: HOURS, sides: [], revision: 7 });
check("nothing to bid says so", !!doc.getElementById("empty"));

console.log(process.exitCode ? "\nSOME CHECKS FAILED" : "\nALL CHECKS PASSED");
