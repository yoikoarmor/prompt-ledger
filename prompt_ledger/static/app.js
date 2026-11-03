"use strict";
const $ = (id) => document.getElementById(id);
let token = "",
  selected = [],
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
  $("clear").disabled = value;
  $("import").textContent = value ? "ローカルで処理中…" : "比較して取り込む →";
}
function selectFiles(files) {
  if (busy) return;
  const list = Array.from(files);
  if (
    list.length > 20 ||
    list.reduce((n, f) => n + f.size, 0) > 20 * 1024 * 1024 ||
    list.some((f) => f.size > 10 * 1024 * 1024)
  )
    return notify(
      "最大20件、1件10 MiB、合計20 MiB以内で選択してください。",
      true,
    );
  if (list.some((f) => !/\.(png|jpe?g|json)$/i.test(f.name)))
    return notify("PNG / JPEG / JSON のみ選択できます。", true);
  selected = list;
  $("selection-label").textContent = list.length
    ? `${list.length} 件を選択 · ${(list.reduce((n, f) => n + f.size, 0) / 1024).toFixed(0)} KiB`
    : "ファイル未選択";
  $("file-list").replaceChildren(
    ...list.map((f) => {
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
  $("model-notice").hidden = status.model_state !== "needs_setup";
  if (status.model?.model_id === "synthetic-test-only") {
    $("model-name").textContent = "Synthetic test";
    $("model-detail").textContent = "合成ベクトル · Qwen 実測ではありません";
  }
  $("more").hidden = data.records.length < 100;
  render();
}
async function encodeFile(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let value = "";
  for (let i = 0; i < bytes.length; i += 32768)
    value += String.fromCharCode(...bytes.subarray(i, i + 32768));
  return { name: file.name, content_base64: btoa(value) };
}
async function ingest(checkOnly) {
  setBusy(true);
  try {
    const files = await Promise.all(selected.map(encodeFile));
    const data = await api("/api/import", {
      method: "POST",
      body: JSON.stringify({
        files,
        dry_run: $("dry-run").checked,
        check_only: checkOnly,
      }),
    }).then((r) => r.json());
    if (checkOnly) {
      const n = data.results.filter((r) => r.status === "parsed").length;
      notify(
        `メタデータ確認: ${n} 件抽出可能 / ${data.results.length - n} 件要確認（保存なし）`,
      );
    } else if (data.dry_run) {
      preview = true;
      $("more").hidden = true;
      records = data.results;
      render();
      $("result-description").textContent =
        "試行結果です。保存していません。表示IDは仮のものです。";
      notify("試行が完了しました。データは保存していません。");
    } else {
      await refresh();
      $("result-description").textContent =
        "直近100件を表示。同一のペアは再登録せず既存の判定を再利用します。";
      const reused = data.results.filter((r) => r.reused).length;
      notify(
        `${data.results.length} 件を処理しました（うち ${reused} 件は既存記録）。`,
      );
    }
  } catch (err) {
    notify(err.message, true);
  } finally {
    setBusy(false);
  }
}
$("files").onchange = (event) => selectFiles(event.target.files);
$("clear").onclick = () => {
  $("files").value = "";
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
