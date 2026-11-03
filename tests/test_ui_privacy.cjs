// Execute the real UI source with a minimal synthetic DOM and deferred HTTP responses.
// This test never reads images or real prompt data and needs only Node's standard library.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

class Element {
  constructor() {
    this.children = [];
    this.checked = false;
    this.style = {};
    this.value = "";
    this.classList = { toggle() {}, add() {}, remove() {} };
  }
  set textContent(value) {
    this.children = [];
    this.text = String(value);
  }
  get textContent() {
    return (
      (this.text || "") +
      this.children
        .map((c) => (typeof c === "string" ? c : c.textContent))
        .join("")
    );
  }
  append(...children) {
    this.children.push(...children);
  }
  replaceChildren(...children) {
    this.text = "";
    this.children = children;
  }
}

async function harness(options = {}) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  let deferred = false;
  const pending = [];
  const pendingRules = [];
  const uploads = [], endings = [];
  const status = {
    counts: { accepted: 1, duplicate: 0 },
    pending_review: 0,
    threshold: 0.95,
    model_state: "configured",
  };
  const response = (value) => ({ ok: true, json: async () => value });
  const context = {
    document: {
      getElementById: element,
      createElement: () => new Element(),
      createTextNode: (text) => ({ textContent: text }),
      querySelectorAll: () => [],
    },
    fetch: async (url, args = {}) => {
      if (url === "/session") return response({ token: "synthetic-token" });
      if (url === "/api/status") return response(status);
      if (url === "/api/dry-batch") return response({batch_id: "synthetic-batch"});
      if (url.startsWith("/api/dry-batch/")) { endings.push(url); return response({rolled_back: true}); }
      if (url.startsWith("/api/file?")) {
        uploads.push({url, body: args.body});
        if (options.cancelAfter === uploads.length) element("cancel").onclick();
        return response({results: [{id: uploads.length, status: "accepted"}]});
      }
      if (url.startsWith("/api/rules")) {
        if (!deferred) return response({ version: 0, count: 0, terms: [] });
        return new Promise((resolve) =>
          pendingRules.push({
            url,
            resolve: (value) => resolve(response(value)),
          }),
        );
      }
      if (url.startsWith("/api/records")) {
        if (!deferred) return response({ records: [] });
        return new Promise((resolve) =>
          pending.push({ url, resolve: (value) => resolve(response(value)) }),
        );
      }
      throw new Error("Unexpected synthetic request");
    },
    setTimeout: () => 0,
    clearTimeout() {},
    console,
    URLSearchParams,
    AbortController,
    DOMException,
  };
  vm.createContext(context);
  const code = fs.readFileSync(
    path.join(__dirname, "../prompt_ledger/static/app.js"),
    "utf8",
  );
  vm.runInContext(
    code +
      "\nglobalThis.testUI = {refresh, loadRules, render, ingest, selectFiles, setRecords: value => records = value, getRecords: () => records};",
    context,
  );
  await new Promise(setImmediate);
  deferred = !options.bulk;
  return { element, pending, pendingRules, uploads, endings, ui: context.testUI };
}

test("folder queue streams large files without arrayBuffer and has no 20-file cap", async () => {
  const {element, uploads, ui} = await harness({bulk: true});
  const files = Array.from({length: 25}, (_, i) => ({name: `${i}.png`, webkitRelativePath: `日本語 folder/${i}.png`, size: 100 * 1024 * 1024,
    arrayBuffer() { throw new Error("Must not buffer images"); }}));
  element("folder").onchange({target: {files}});
  await ui.ingest(false);
  assert.equal(uploads.length, 25);
  assert.equal(uploads[0].body, files[0]);
  assert.equal(new URLSearchParams(uploads[0].url.split("?")[1]).get("name"), "日本語 folder/0.png");
});

test("cancel stops queue and closes dry-run transaction", async () => {
  const {element, uploads, endings, ui} = await harness({bulk: true, cancelAfter: 1});
  element("dry-run").checked = true;
  ui.selectFiles([{name: "a.json", size: 10}, {name: "b.json", size: 10}]);
  await ui.ingest(false);
  assert.equal(uploads.length, 1);
  assert.equal(endings.length, 1);
  assert.equal(element("cancel").disabled, true);
});

const rich = {
  id: 1,
  status: "accepted",
  reason: "new_pair",
  nearest_id: null,
  source: "synthetic-private-name.json",
  positive: "synthetic private flower",
  negative: "synthetic private blur",
  matched_terms: ["synthetic secret rule"],
  original_matched_terms: ["synthetic historical rule"],
};
const safe = {
  id: 1,
  status: "accepted",
  reason: "new_pair",
  nearest_id: null,
};

test("privacy off immediately clears text/source and ignores the later stale rich response", async () => {
  const { element, pending, ui } = await harness();
  element("show-text").checked = true;
  ui.setRecords([rich]);
  ui.render();
  assert.match(element("result-list").textContent, /synthetic private flower/);
  const oldRequest = ui.refresh();
  element("show-text").checked = false;
  const newRequest = element("show-text").onchange();
  assert.doesNotMatch(
    element("result-list").textContent,
    /synthetic private|synthetic-private/,
  );
  assert.equal(ui.getRecords()[0].source, undefined);
  assert.equal(ui.getRecords()[0].matched_terms, undefined);
  assert.equal(ui.getRecords()[0].original_matched_terms, undefined);
  assert.doesNotMatch(element("result-list").textContent, /synthetic secret|synthetic historical/);
  assert.match(pending[0].url, /privacy=false/);
  assert.match(pending[1].url, /privacy=true/);
  pending[1].resolve({ records: [safe] });
  await newRequest;
  pending[0].resolve({ records: [rich] });
  await oldRequest;
  assert.doesNotMatch(
    element("result-list").textContent,
    /synthetic private|synthetic-private/,
  );
  assert.equal(ui.getRecords()[0].positive, undefined);
});

test("privacy off clears rule editor and drops late sensitive rule responses", async () => {
  const { element, pending, pendingRules, ui } = await harness();
  element("show-text").checked = true;
  element("rules-terms").value = "synthetic secret rule";
  element("rules-form").hidden = false;
  const old = ui.loadRules();
  element("show-text").checked = false;
  const current = element("show-text").onchange();
  assert.equal(element("rules-terms").value, "");
  assert.equal(element("rules-form").hidden, true);
  pendingRules[1].resolve({ version: 1, count: 1 });
  pending[0].resolve({ records: [safe] });
  await current;
  pendingRules[0].resolve({
    version: 1,
    count: 1,
    terms: ["synthetic secret rule"],
  });
  await old;
  assert.equal(element("rules-terms").value, "");
  assert.equal(element("rules-form").hidden, true);
});

test("hidden mode gates filenames even if stale rich data is rendered directly", async () => {
  const { element, ui } = await harness();
  element("show-text").checked = false;
  ui.setRecords([rich]);
  ui.render();
  assert.doesNotMatch(
    element("result-list").textContent,
    /synthetic private|synthetic-private/,
  );
});
