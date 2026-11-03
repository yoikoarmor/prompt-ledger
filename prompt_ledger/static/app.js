"use strict";
const $ = (id) => document.getElementById(id);
let token = "",
  selected = [],
  importController = null,
  records = [],
  filter = "all",
  busy = false,
  preview = false,
  refreshGeneration = 0,
  rulesGeneration = 0,
  rulesVersion = null,
  reviewId = null;
const labels = {
  accepted: "採用",
  duplicate: "重複",
  review: "要確認",
  excluded: "除外",
};
const reasons = {
  exclusion_rule_match: "現在の除外ルールに一致したため、JSONに含めません。",
  excluded_at_import:
    "取り込み時の除外記録です。ルール解除後は元ファイルを再取り込みしてください。",
  new_pair: "新しいプロンプトの組み合わせです。",
  both_cosines_at_or_above_threshold:
    "Positive・Negative の両方がしきい値以上です。",
  review: "メタデータを一意に特定できませんでした。",
  format: "未対応、または不正なメタデータです。",
  invalid_json: "JSON の形式を確認してください。",
  token_limit: "トークン数が上限を超えています。切り詰めはしていません。",
  metadata_limit: "メタデータがサイズ上限を超えています。",
  text_limit: "プロンプト本文がサイズ上限を超えています。",
};
function notify(text, error = false) {
  const el = $("message");
  el.textContent = text;
  el.classList.toggle("error", error);
  el.hidden = false;
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => (el.hidden = true), 7000);
}
async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      "X-Ledger-Token": token,
      "Content-Type": "application/json",
      ...options.headers,
    },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(
      err.error?.message || `処理できませんでした (${res.status})`,
    );
  }
  return res;
}
function setBusy(value) {
  busy = value;
  $("import").disabled = value || !selected.length;
  $("check").disabled = value || !selected.length;
  $("files").disabled = value;
  $("folder").disabled = value;
  $("cancel").disabled = !value;
  $("clear").disabled = value;
  $("import").textContent = value ? "ローカルで処理中…" : "比較して取り込む →";
}
function selectFiles(files) {
  if (busy) return;
  const list = Array.from(files).filter(f => /\.(png|jpe?g|json)$/i.test(f.name));
  selected = list;
  $("selection-label").textContent = list.length
    ? `${list.length} 件を選択 · ${(list.reduce((n, f) => n + f.size, 0) / 1024).toFixed(0)} KiB`
    : "ファイル未選択";
  $("file-list").replaceChildren(
    ...list.slice(0, 100).map((f) => {
      const li = document.createElement("li");
      li.textContent = f.name;
      return li;
    }),
  );
  setBusy(false);
}
function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}
function render() {
  const rows = records.filter((r) => filter === "all" || r.status === filter ||
    (filter === "review" && r.stored_status === "review"));
  const target = $("result-list");
  target.replaceChildren();
  if (!rows.length) {
    const empty = node("div", "empty");
    empty.append(
      node("span", "", "≋"),
      node(
        "h3",
        "",
        records.length
          ? "この分類の記録はありません"
          : "コレクションはここから。",
      ),
      node("p", "", "選択したファイルの判定がここに表示されます。"),
    );
    target.append(empty);
    return;
  }
  for (const r of rows) {
    const card = node("article", "record");
    const head = node("div", "record-head");
    head.append(
      node("span", `badge ${r.status}`, labels[r.status] || r.status),
      node("span", "record-id", `#${r.id}`),
    );
    if ($("show-text").checked && r.source)
      head.append(node("span", "record-name", r.source));
    if ((r.status === "review" || r.stored_status === "review") && !r.resolved_by && !preview) {
      const button = node(
        "button",
        "text-button review-button",
        "確認・補正 →",
      );
      button.onclick = () => {
        reviewId = r.id;
        $("review-positive").value = r.positive || "";
        $("review-negative").value = r.negative || "";
        $("review-dialog").showModal();
      };
      head.append(button);
    }
    card.append(
      head,
      node(
        "p",
        "record-reason",
        r.resolved_by
          ? `補正済み → #${r.resolved_by}`
          : reasons[r.reason] || r.reason,
      ),
    );
    if (r.nearest_id != null) {
      const scores = node("div", "scores");
      scores.append(node("span", "", `近傍 #${r.nearest_id}`));
      for (const [label, score] of [
        ["POS", r.positive_score],
        ["NEG", r.negative_score],
      ]) {
        const span = node("span", "", label);
        span.append(node("strong", "", score.toFixed(4)));
        scores.append(span);
      }
      const track = node("div", "score-track"),
        bar = node("div");
      bar.style.width = `${Math.max(0, Math.min(r.positive_score, r.negative_score)) * 100}%`;
      track.append(bar);
      card.append(scores, track);
    }
    if ($("show-text").checked && r.positive != null)
      for (const [label, text] of [
        ["POSITIVE", r.positive],
        ["NEGATIVE", r.negative],
      ]) {
        const block = node("div", "prompt-text");
        block.append(
          node("b", "", label),
          document.createTextNode(text || "（空）"),
        );
        card.append(block);
      }
    if (
      $("show-text").checked &&
      (r.matched_terms?.length || r.original_matched_terms?.length)
    ) {
      card.append(
        node(
          "p",
          "record-reason",
          "一致文字列: " +
            (r.matched_terms?.length
              ? r.matched_terms
              : r.original_matched_terms
            ).join(" / "),
        ),
      );
    }
    target.append(card);
  }
}
async function refresh(append = false) {
  const generation = ++refreshGeneration;
  const privacy = !$("show-text").checked;
  const offset = append ? records.length : 0;
  const [status, data] = await Promise.all([
    api("/api/status").then((r) => r.json()),
    api(`/api/records?privacy=${privacy}&limit=100&offset=${offset}`).then(
      (r) => r.json(),
    ),
  ]);
  if (generation !== refreshGeneration || privacy !== !$("show-text").checked)
    return;
  preview = false;
  records = append ? records.concat(data.records) : data.records;
  $("accepted-count").textContent = status.counts.accepted;
  $("duplicate-count").textContent = status.counts.duplicate;
  $("review-count").textContent = status.pending_review;
  $("threshold").textContent = status.threshold.toFixed(2);
  if (status.execution) {
    $("device-mode").textContent = `${status.execution.requested} → ${status.execution.selected || "未選択"} (${status.execution.loaded ? "ロード済み" : "未ロード"})`;
  }
  $("model-notice").hidden = status.model_state !== "needs_setup";
  $("runtime-notice").textContent = status.model_error === "cuda_unavailable"
    ? "CUDAが利用できません。CUDA版依存・ドライバを確認するか、cpu / auto を選んで再起動してください。"
    : "READMEのsetup手順を確認してください。メタデータ確認はモデルなしでも利用できます。";
  if (status.model?.model_id === "synthetic-test-only") {
    $("model-name").textContent = "Synthetic test";
    $("model-detail").textContent = "合成ベクトル · Qwen 実測ではありません";
  }
  $("more").hidden = data.records.length < 100;
  render();
}
async function ingest(checkOnly) {
  setBusy(true);
  importController = new AbortController();
  let batchId = null, processed = 0;
  const counts = {}, previews = [];
  try {
    if (!checkOnly && $("dry-run").checked) {
      batchId = (await api("/api/dry-batch", {method: "POST"}).then(r => r.json())).batch_id;
    }
    for (const file of selected) {
      if (importController.signal.aborted) throw new DOMException("Cancelled", "AbortError");
      const params = new URLSearchParams({name: file.webkitRelativePath || file.name, check_only: String(checkOnly)});
      if (batchId) params.set("batch_id", batchId);
      const data = await api(`/api/file?${params}`, {
        method: "POST", body: file, headers: {"Content-Type": "application/octet-stream"},
        signal: importController.signal,
      }).then(r => r.json());
      const result = data.results[0];
      counts[result.status] = (counts[result.status] || 0) + 1;
      if (previews.length < 200) previews.push(result);
      processed++;
      $("selection-label").textContent = `${processed} / ${selected.length} 件処理済み`;
    }
    if (batchId) {
      await api(`/api/dry-batch/${batchId}`, {method: "DELETE"});
      batchId = null;
      preview = true; records = previews; render(); $("more").hidden = true;
    } else if (!checkOnly) await refresh();
    notify(`${processed} 件完了 / ${Object.entries(counts).map(([k,v]) => `${labels[k] || k}: ${v}`).join(" / ")}`);
  } catch (err) {
    notify(err.name === "AbortError" ? "中止しました。通常取込の処理済み記録は保持します。試行の中止を要求しました。実行中の推論終了後にrollbackされます。" : err.message, true);
  } finally {
    if (batchId) await api(`/api/dry-batch/${batchId}`, {method: "DELETE"}).catch(() => {});
    importController = null;
    setBusy(false);
  }
}
$("folder").onchange = (event) => selectFiles(event.target.files);
$("cancel").onclick = () => importController?.abort();
$("files").onchange = (event) => selectFiles(event.target.files);
$("clear").onclick = () => {
  $("files").value = "";
  $("folder").value = "";
  selectFiles([]);
};
$("dropzone").ondragover = (event) => {
  event.preventDefault();
  $("dropzone").classList.add("dragover");
};
$("dropzone").ondragleave = () => $("dropzone").classList.remove("dragover");
$("dropzone").ondrop = (event) => {
  event.preventDefault();
  $("dropzone").classList.remove("dragover");
  selectFiles(event.dataTransfer.files);
};
$("import").onclick = () => ingest(false);
$("check").onclick = () => ingest(true);
$("show-text").onchange = () => {
  ++refreshGeneration;
  if (!$("show-text").checked) {
    records = records.map(
      ({
        source,
        positive,
        negative,
        matched_terms,
        original_matched_terms,
        ...safe
      }) => safe,
    );
    $("review-positive").value = "";
    $("review-negative").value = "";
    ++rulesGeneration;
    $("rules-terms").value = "";
    $("rules-form").hidden = true;
    rulesVersion = null;
  }
  render();
  loadRules().catch((e) => notify(e.message, true));
  return refresh().catch((e) => notify(e.message, true));
};
async function loadRules() {
  const generation = ++rulesGeneration;
  const privacy = !$("show-text").checked;
  const data = await api(`/api/rules?privacy=${privacy}`).then((r) => r.json());
  if (generation !== rulesGeneration || privacy !== !$("show-text").checked)
    return;
  rulesVersion = data.version;
  $("rules-summary").textContent =
    `${data.count} 件登録 · version ${data.version}`;
  $("rules-form").hidden = privacy;
  $("rules-private").hidden = !privacy;
  $("rules-terms").value = privacy ? "" : data.terms.join("\n");
}
$("rules-reload").onclick = () =>
  loadRules().catch((e) => notify(e.message, true));
$("rules-form").onsubmit = async (event) => {
  event.preventDefault();
  if (!$("show-text").checked || rulesVersion === null) return;
  $("rules-save").disabled = true;
  try {
    await api("/api/rules", {
      method: "PUT",
      body: JSON.stringify({
        terms: $("rules-terms")
          .value.split(/\r?\n/)
          .filter((t) => t.trim()),
        expected_version: rulesVersion,
      }),
    });
    ++refreshGeneration;
    records = [];
    render();
    await Promise.all([loadRules(), refresh()]);
    notify("除外ルールを保存しました。今後の書き出しにも適用します。");
  } catch (e) {
    notify(e.message, true);
  } finally {
    $("rules-save").disabled = false;
  }
};
$("more").onclick = () => refresh(true).catch((e) => notify(e.message, true));
document.querySelectorAll(".tab").forEach(
  (tab) =>
    (tab.onclick = () => {
      filter = tab.dataset.filter;
      document.querySelectorAll(".tab").forEach((t) => {
        t.classList.toggle("active", t === tab);
        t.setAttribute("aria-selected", String(t === tab));
      });
      render();
    }),
);
$("export").onclick = async () => {
  try {
    if (window.showSaveFilePicker) {
      const handle = await window.showSaveFilePicker({suggestedName: "accepted.json"});
      const response = await api("/api/export");
      let writable;
      try {
        writable = await handle.createWritable();
      } catch (error) {
        await response.body.cancel().catch(() => {});
        throw error;
      }
      await response.body.pipeTo(writable);
      notify("JSON export completed.");
      return;
    }
    const blob = await api("/api/export").then((r) => r.blob());
    const link = document.createElement("a"),
      url = URL.createObjectURL(blob);
    link.href = url;
    link.download = "accepted.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    notify("採用されたプロンプト本文を含む JSON を保存しました。");
  } catch (e) {
    notify(e.message, true);
  }
};
$("close-review").onclick = () => $("review-dialog").close();
$("review-form").onsubmit = async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    await api(`/api/review/${reviewId}`, {
      method: "POST",
      body: JSON.stringify({
        positive: $("review-positive").value,
        negative: $("review-negative").value,
      }),
    });
    $("review-dialog").close();
    await refresh();
    await loadRules();
    notify("補正したペアを再判定しました。");
  } catch (err) {
    notify(err.message, true);
  } finally {
    button.disabled = false;
  }
};
(async () => {
  try {
    token = (await fetch("/session").then((r) => r.json())).token;
    await refresh();
    await loadRules();
  } catch (err) {
    notify(`接続を確認してください: ${err.message}`, true);
  }
})();
