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
  contains(other) { return other === this || this.children.includes(other); }
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
  const documentEvents = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  let deferred = false;
  const pending = [];
  const pendingRules = [];
  const uploads = [], endings = [], writes = [];
  const sink = {closed: false, aborted: false};
  const status = {
    counts: { accepted: 1, duplicate: 0 },
    pending_review: 0,
    threshold: 0.95,
    model_state: "configured",
  };
  const response = (value) => ({ ok: true, json: async () => value });
  const context = {
    window: {showSaveFilePicker: async () => ({createWritable: async () => ({write: async text => {writes.push(text); if (options.onWrite) await options.onWrite(text,element);}, close: async () => {sink.closed=true;}, abort: async () => {sink.aborted=true;}})})},
    document: {
      addEventListener: (type, handler) => documentEvents.set(type, handler),
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
        if (url.includes("extract_only=true")) return response({rule_version: 0, record: {source: "synthetic.json", status: "extracted", positive: "synthetic private text", negative: ""}});
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
      "\nglobalThis.testUI = {setBusy, refresh, loadRules, render, ingest, selectFiles, setRecords: value => records = value, getRecords: () => records};",
    context,
  );
  await new Promise(setImmediate);
  deferred = !options.bulk;
  return { documentEvents, element, pending, pendingRules, uploads, endings, writes, sink, ui: context.testUI };
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


test("explicit extraction streams text JSON without putting body into records", async () => {
  const {element, uploads, writes, ui} = await harness({bulk: true});
  ui.selectFiles([{name: "synthetic.json", size: 10}]);
  await element("extract").onclick();
  const result = JSON.parse(writes.join(""));
  assert.equal(result.kind, "metadata-extraction");
  assert.equal(result.records[0].positive, "synthetic private text");
  assert.equal(uploads.length, 1);
  assert.equal(ui.getRecords().length, 0);
  assert.ok(!element("result-list").textContent.includes("synthetic private text"));
});


for (const phase of ["record", "final-json"]) {
  test(`extraction cancellation during ${phase} write aborts without closing`, async () => {
    const {element, sink, ui} = await harness({bulk: true, onWrite: async (text, get) => {
      if ((phase === "record" && text.includes("synthetic private text")) || (phase === "final-json" && text === "]}\n")) {
        get("cancel").onclick();
        await new Promise(setImmediate);
      }
    }});
    ui.selectFiles([{name: "synthetic.json", size: 10}]);
    await element("extract").onclick();
    assert.equal(sink.closed,false);
    assert.equal(sink.aborted,true);
    assert.match(element("message").textContent,/cancelled/);
    assert.ok(!element("message").textContent.includes("exported"));
  });
}

function syntheticDrop(dataTransfer) {
  return {dataTransfer, prevented: false, stopped: false,
    preventDefault() {this.prevented = true;}, stopPropagation() {this.stopped = true;}};
}
const syntheticFile = name => ({name, size: 12, arrayBuffer() {throw Error("Must not read file bytes while selecting");}});

test("dropzone selects local files once without uploading or reading bytes", async () => {
  const {element,uploads}=await harness({bulk:true});
  const event=syntheticDrop({types:["Files"],files:[syntheticFile("a.PNG"),syntheticFile("b.json")]});
  element("dropzone").ondrop(event);
  assert.equal(event.prevented,true);assert.equal(event.stopped,true);
  assert.match(element("selection-label").textContent,/2/);
  assert.equal(element("import").disabled,false);assert.equal(uploads.length,0);
  assert.match(element("message").textContent,/2 件を選択/);
});
test("file drop outside zone is selected and prevents browser file navigation", async () => {
  const {documentEvents,element}=await harness({bulk:true});
  const event=syntheticDrop({types:["Files"],files:[syntheticFile("outside.json")]});
  documentEvents.get("drop")(event);
  assert.equal(event.prevented,true);assert.equal(event.stopped,true);
  assert.match(element("file-list").textContent,/outside.json/);
});
test("drop falls back to file items synchronously when files list is empty", async () => {
  const {element}=await harness({bulk:true});let reads=0;
  const event=syntheticDrop({types:["Files"],files:[],items:[{kind:"file",getAsFile(){reads++;return syntheticFile("item.json");}}]});
  element("dropzone").ondrop(event);
  assert.equal(reads,1);assert.match(element("file-list").textContent,/item.json/);
});
test("unsupported and URL-only drops explain failure without clearing selection", async () => {
  const {element,ui}=await harness({bulk:true});ui.selectFiles([syntheticFile("keep.json")]);
  for(const transfer of [{types:["Files"],files:[syntheticFile("notes.txt")]},{types:["text/uri-list"],files:[]}]) {
    element("dropzone").ondrop(syntheticDrop(transfer));
    assert.match(element("message").textContent,/対応するローカルファイルがありません/);
    assert.match(element("file-list").textContent,/keep.json/);
  }
});
test("mixed formats report skipped files and keep supported selection", async () => {
  const {element}=await harness({bulk:true});
  element("dropzone").ondrop(syntheticDrop({files:[syntheticFile("yes.json"),syntheticFile("no.txt")]}));
  assert.match(element("file-list").textContent,/yes.json/);
  assert.doesNotMatch(element("file-list").textContent,/no.txt/);
  assert.match(element("message").textContent,/未対応形式 1 件/);
});
test("busy drop is prevented with feedback and preserves active queue", async () => {
  const {element,ui,documentEvents}=await harness({bulk:true});
  ui.selectFiles([syntheticFile("active.json")]);ui.setBusy(true);
  const event=syntheticDrop({types:["Files"],files:[syntheticFile("new.json")]});
  documentEvents.get("drop")(event);
  assert.equal(event.prevented,true);assert.match(element("message").textContent,/処理中/);
  assert.match(element("file-list").textContent,/active.json/);
  assert.equal(element("import").disabled,true);
});
test("document handles file drags but leaves ordinary text drag alone", async () => {
  const {documentEvents}=await harness({bulk:true});
  const text=syntheticDrop({types:["text/plain"],files:[]});
  documentEvents.get("dragover")(text);documentEvents.get("drop")(text);
  assert.equal(text.prevented,false);
  const file=syntheticDrop({types:["Files"],files:[],dropEffect:"none"});
  documentEvents.get("dragover")(file);
  assert.equal(file.prevented,true);assert.equal(file.dataTransfer.dropEffect,"copy");
});

function fileEntry(path) {
 const name=path.split('/').pop();
 return {name,fullPath:path,isFile:true,isDirectory:false,file:ok=>ok(syntheticFile(name))};
}
function directoryEntry(path,batches) {
 return {name:path.split('/').pop(),fullPath:path,isDirectory:true,isFile:false,
   createReader:()=>{let index=0;return {readEntries:ok=>ok(batches[index++] || [])};}};
}
test("parent folder drop reads all batches and both nested folders with relative upload paths",async()=>{
 const {element,uploads,ui}=await harness({bulk:true});
 const many=Array.from({length:101},(_,i)=>fileEntry(`/img/imgfolder1/${i}.png`));
 const one=directoryEntry('/img/imgfolder1',[many.slice(0,100),many.slice(100),[]]);
 const two=directoryEntry('/img/imgfolder2',[[fileEntry('/img/imgfolder2/new.json')],[]]);
 const root=directoryEntry('/img',[[one,two],[]]);
 await element('dropzone').ondrop(syntheticDrop({types:['Files'],items:[{kind:'file',webkitGetAsEntry:()=>root}],files:[]}));
 assert.match(element('selection-label').textContent,/102/);
 assert.equal(uploads.length,0);
 await ui.ingest(true);
 assert.equal(uploads.length,102);
 const names=uploads.map(u=>new URLSearchParams(u.url.split('?')[1]).get('name'));
 assert.ok(names.includes('img/imgfolder1/100.png'));assert.ok(names.includes('img/imgfolder2/new.json'));
 assert.ok(names.every(n=>!n.startsWith('/')&&!n.includes('..')));
});
test("folder picker keeps nested sibling paths for every selected file",async()=>{
 const {element,ui,uploads}=await harness({bulk:true});
 element('folder').onchange({target:{files:[{...syntheticFile('a.png'),webkitRelativePath:'img/imgfolder1/a.png'},{...syntheticFile('b.json'),webkitRelativePath:'img/imgfolder2/b.json'}]}});
 await ui.ingest(true);
 assert.deepEqual(uploads.map(u=>new URLSearchParams(u.url.split('?')[1]).get('name')),['img/imgfolder1/a.png','img/imgfolder2/b.json']);
});
for(const issue of ['cycle','outside','dot-name','link','reader-failure']) {
 test(`unsafe directory drop ${issue} fails without replacing selection or uploading`,async()=>{
  const {element,ui,uploads}=await harness({bulk:true});ui.selectFiles([syntheticFile('keep.json')]);
  let child;
  if(issue==='outside')child=fileEntry('/outside/private.png');
  else if(issue==='dot-name')child={...fileEntry('/img/../private.png'),name:'../private.png'};
  else if(issue==='link')child={...fileEntry('/img/link.png'),isSymbolicLink:true};
  else child=fileEntry('/img/a.png');
  const root=directoryEntry('/img',[[child],[]]);
  if(issue==='cycle')root.createReader=()=>({readEntries:ok=>ok([root])});
  if(issue==='reader-failure')root.createReader=()=>({readEntries:(ok,fail)=>fail(Error('synthetic permission error'))});
  await element('dropzone').ondrop(syntheticDrop({types:['Files'],items:[{kind:'file',webkitGetAsEntry:()=>root}],files:[]}));
  assert.match(element('file-list').textContent,/keep.json/);assert.equal(uploads.length,0);
  assert.match(element('message').textContent,/安全に読み取れません/);assert.equal(element('files').disabled,false);
 });
}
test("folder scan cancellation ends even while browser reader callback is pending",async()=>{
 const {element,ui}=await harness({bulk:true});ui.selectFiles([syntheticFile('keep.json')]);
 let callback;
 const root=directoryEntry('/img',[]);root.createReader=()=>({readEntries:ok=>{callback=ok;}});
 const pending=element('dropzone').ondrop(syntheticDrop({types:['Files'],items:[{kind:'file',webkitGetAsEntry:()=>root}],files:[]}));
 assert.equal(element('cancel').disabled,false);
 element('cancel').onclick();await pending;
 callback([fileEntry('/img/late.png')]);await new Promise(setImmediate);
 assert.match(element('file-list').textContent,/keep.json/);assert.match(element('message').textContent,/中止/);
 assert.equal(element('files').disabled,false);
});
