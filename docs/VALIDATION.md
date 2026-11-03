# Compared JSON / extraction cancellation follow-up

161 Python tests passed, 1 skipped; Node UI 8 passed; Ruff and JS syntax passed.
New CLI/API flow tests start with existing accepted data and import synthetic PNG
and JSON pairs. They verify both cosines exactly .95 rejects, changed negative
accepts, repeated input reuses a row, missing metadata reviews, blocked text is
excluded, and accepted-only JSON includes existing records under current rules.
Extraction remains a separate optional no-comparison mode; normal documentation/UI
now prioritize compared import followed by accepted JSON export.
Fixed independent-review P2: extraction captures a stable AbortSignal and checks it
before/after writes and before final commit. Cancel during the last record write or
JSON tail write aborts the writable and does not close/save or report success.
Once OS close begins, cancellation is disabled; reversal of OS commit is not promised.
No real images/prompts or GPU were used in these regressions. No commit/push.

# Bounded comparison / CUDA batching follow-up

159 synthetic Python tests passed, 1 skipped; Node UI 6 passed; Ruff and JS syntax passed.
128-row blocks and per-query 64-entry decoded-vector cache avoid all-N-by-N storage.
Tests cover exact 0.95, ID ties across blocks, empty negatives, excluded rows, corrupt
vectors, memory budget with 1500 synthetic rows, no-NumPy fallback, GPU score/failure
stubs, rounding near threshold, cancellation rollback and pair token/batch budgets.
CUDA pair embedding is capped at two texts / 4096 padded tokens, otherwise batch1;
CUDA batching identity prevents mixing previous CUDA-space vectors. CPU space unchanged.
New GPU inference/comparison paths have not been measured on hardware: GPU remained
busy (35-45% utilization, about 6-7 GiB) so no GPU test was started. Prior single-text
CUDA smoke does not verify this revision. No speed or VRAM claims for GPU are made.
Selected-folder CPU timing and private derived vector caches remain in ignored .cache.
No real prompt text, image bytes, DB or vectors are included in tracked source/artifacts.

# Metadata extraction follow-up

138 synthetic Python tests passed, 1 skipped; 6 Node UI tests passed; Ruff and JS
syntax passed. Tests cover embedding-free CLI/API, authorization, repeated pairs,
exclusion privacy, malformed metadata, atomic interruption cleanup and UI direct-save
without copying returned prompt text into the records display. Current comparison
algorithm is unchanged. Source is held for independent review; no commit/push.
Selected-folder validation reports remain isolated in ignored .cache, never source.

# CUDA setup path verification

Existing setup.bat forwards --cuda and selects the exclusive cuda extra. No missing
installer path was found. Official cu130 index lists pinned Windows wheels for Python
3.10-3.13. Offline uv 0.11.7 dry-run against the existing CPU venv planned removal of
torch 2.14.1+cpu and installation of 2.14.1+cu130. An empty-target dry-run planned
45 packages including CUDA torch. No package was downloaded/installed by these checks.
Setup synthetic suite: 10 passed, covering fresh/existing and CPU/CUDA selections.
GPU inference was not attempted. Documentation now describes both switch directions.

# ASGI disconnect cleanup follow-up

130 Python tests passed, 1 skipped. Ruff, JS syntax and 5 Node UI tests passed.
ClosingStreamingResponse owns body_iterator.aclose() in a shielded finally around
its complete ASGI call. Regression tests cover ASGI 2.3 disconnect cancellation,
ASGI 2.4 OSError and general send exception. Each retains the response/iterator
without garbage collection and immediately commits a separate SQLite writer.
Browser createWritable failure now cancels the fetched response body explicitly.
No real input, model inference or GPU was used. No commit/push performed.

# Streaming export follow-up

127 Python tests passed, 1 skipped. Synthetic 2000 rows with 8192-character bodies
produced more than 16 MB while tracemalloc peak stayed below 2 MB (Python heap only,
not SQLite native RSS or browser memory). Format/count compatibility, exclusion
snapshot under concurrent rule edit, generator close, interrupted atomic-file cleanup
and previous-file preservation passed. API uses the same generator with finally cleanup.
Real images/prompts/GPU were not used. Browser direct-save branch requires a supporting
browser; the Blob compatibility branch still materializes output.

# Independent review follow-up

123 synthetic Python tests passed, 1 skipped (symlink privilege). Node UI 5 passed.
Dry-run enqueue/close/final drain now share a mutex, so accepted futures cannot be
stranded after worker shutdown. Regression fixes the enqueue/close interleaving.
Per-folder-entry containment/stat OSError becomes input_io and preserves prior
accepted entries; both failures tested. Export memory limitation remains explicit
in BULK_CUDA_JA.md; streaming export is deferred. No real input or GPU used.

# Current bulk-input and CUDA validation (2026-11-03)

This section supersedes historical status below. Current changes remain uncommitted
and await the parent review. No push or remote modification was performed.

- Full synthetic Python suite: 120 passed, 1 skipped (Windows file-symlink privilege).
- Node UI suite: 5 passed, including streaming 25 large synthetic file objects,
  cancellation/rollback cleanup, and all three existing privacy race cases.
- Ruff source/test/setup checks and JavaScript syntax check passed.
- Browser: explicitly selected a synthetic two-JSON-file folder and imported it
  sequentially; progress showed 2/2, parse failure went to review, hidden prompt/source
  remained hidden. No real images, prompt history or GPU workloads were used.
- PNG 32 MiB pixel payload and JPEG 32 MiB scan payload were discarded incrementally;
  metadata buffers stayed bounded. 250 synthetic folder entries processed without
  the old 20-file ceiling. Corruption, metadata oversize, permission failure,
  Japanese/space/long relative paths, junction cycle and transaction cancellation tested.
- CUDA device selection, auto CPU fallback and VRAM failure tested with stubs only.
  Official PyTorch CUDA 13.0 dependency index is pinned in uv.lock; no CUDA install
  or real GPU inference was performed. GPU/driver compatibility and VRAM use remain
  unverified. Existing CPU Qwen smoke from the preceding setup task remains separate.
- Metadata/text/model budgets remain explicit; legacy base64 JSON API keeps a
  transport budget. Unlimited image-size input uses /api/file. No silent truncation.
- Memory is bounded for streamed image payloads, not all application operations:
  directory sorting/browser FileList scale with entries, exact comparison runtime
  grows with accepted records, and JSON export still materializes its result.

## Historical implementation record

# Implementation validation — 2026-11-02

## 2026-11-03 exclusion feature follow-up

Final pre-private-upload fixes address both confirmed P2 findings from the real
Claude Code Opus 5.5 review of tree c0044f1: resolution now checks the target's
stored accepted/duplicate status and prohibits self links; excluded corrections
leave their original reviews pending for explicit retry after rule removal.
Excluded stored reviews remain accessible in the UI review tab.
Final local regression: **80 Python tests passed**, **3 UI privacy tests passed**;
Ruff and JavaScript syntax passed. New tests cover self/other-review mislinks and
the API retry path for unparseable source metadata, with history preserved.
The repair itself has local regression coverage; no second Opus review is claimed.

Added versioned literal case-insensitive exclusion rules, shared by UI/API/CLI.
Rules default empty, apply before embedding and to all accepted exports under the
current snapshot, and preserve original records/history. Tests cover model-free
exclusion, persistence, positive/negative, literal rather than regex matching,
Unicode casefold, exact reimport, rule removal/reimport, existing accepted export,
review correction, dry-run, invalid rules, optimistic edit conflicts, token protection,
and API/CLI cross-interface use. Privacy regression tests also cover rule editor
clearing and late rule responses. The earlier Opus review covered the pre-feature
snapshot only; this feature is not claimed to have been reviewed by Opus.

- Full regression: **78 passed**, 1 upstream Starlette/httpx deprecation warning.
- Ruff and JavaScript syntax passed; all **3** UI privacy race tests passed.
- Synthetic browser check: saving two rules changed an existing accepted record
  to excluded; hiding text cleared the rule editor and removed filename, prompt,
  and matched-term output. No real images or prompt history were accessed.

Qwen remains unacquired due to the previously documented TLS certificate error;
no further model network attempts or security changes were made.

## Completed

Cache-junction review repair: no-follow lstat traversal now rejects existing links
and reparse points throughout the model cache, before setup writes and before each
download call. New isolated fixtures verify cache-root, huggingface, nested download,
metadata, lock and partial junctions leave their synthetic outside target unchanged.
The independent review confirmed the previous interrupted fixture did not leave a
junction/process running and removed its own new repro junction without touching
the target. This repair does not change authentication, TLS or repository visibility.
Final regression: 106 passed, 1 skipped (Windows file-symlink privilege unavailable),
3 UI tests passed; all six actual junction cases executed and passed. Ruff passed.

Independent-review follow-up: setup no longer trusts a local manifest or resume
hashes as evidence of official provenance. Every invocation requires fresh exact-
revision official metadata. Empty/malformed inventories fail without publishing
a success manifest. Regression checks: 99 passed, 1 deselected (the interrupted
Windows junction check was not repeated without its original outcome record),
3 UI privacy tests passed, Ruff passed. Related review-process lookup found no
running Python/cmd process; the saved independent-review repro contains four
completed non-junction cases and no junction outcome. Commit/push remains held
for independent review. No network, authentication or visibility changes were made
during this repair; these new regression cases use synthetic provider metadata.

2026-11-03 complete-setup follow-up: default setup now includes the pinned official
Qwen model. Real acquisition succeeded with TLS validation unchanged: 10 files,
1,207,470,234 bytes, revision 97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3.
Official LFS SHA-256/Git blob hashes matched; a second invocation verified all
local SHA-256 hashes and skipped downloads. Earlier TLS failure is historical.
Synthetic regressions: 91 Python tests and 3 UI tests passed, including partial
resume, corrupted-file repair, unknown-file preservation, disk space, lock and
TLS-failure behavior. Real prompt/image data was not inspected.
Actual Qwen smoke also passed using three short synthetic strings, CPU float32,
2 threads, batch 1 and offline settings. All vectors were finite, dimension 1024,
L2 norm 1; changed-negative cosine was 0.5168548052 and that pair was accepted.
Exact reimport reused the row without encoding. Total elapsed 26.32 seconds,
first load/encode 25.96 seconds, peak working set 3.68 GiB, minimum available
RAM 14.47 GiB. This is not a real-data semantic-quality or long-context benchmark.
The actual default setup entry also completed successfully: pinned uv 0.11.7
was fetched from official PyPI (25.4 MB wheel), 45 installed model dependencies
were checked against the lock, then existing model hashes passed and download
was skipped. Dependency transfer size on a new machine depends on its platform
and cache; no fixed total dependency download size is claimed.

- Windows, Python 3.10.11, dedicated project `.venv`.
- `pytest -q`: **62 passed**, no skips, 1 upstream Starlette/httpx deprecation warning
  (the warning recommends a future test transport change; current tests pass).
- Ruff checks passed; Python compilation and `node --check` passed.
- `uv.lock` resolved 65 packages from official PyPI and the official PyTorch CPU index.
  Core, development and the CPU model extra are now installed in this dedicated `.venv`.
  No real model weights were loaded.
- Mathematical synthetic vectors verified exact 0.95 rejection, below-boundary acceptance,
  negative mismatch, empty negatives, same-batch behavior and exact pair reuse.
- Model revision/threshold incompatibility, invalid vectors, token-limit logic and offline/CPU
  constructor settings verified with an explicit stub. These are not Qwen semantic benchmarks.
- Synthetic metadata: PNG tEXt/iTXt/zTXt, bounded decompression, JPEG EXIF UserComment,
  direct Comfy graph, custom/multiple sampler rejection, invalid/duplicate-key JSON.
- Relative-root behavior tested from another CWD with Japanese and spaces in the root.
  Path traversal, Windows junctions, concurrent imports, transaction rollback and atomic export tested.
- API tests cover same core, privacy defaults, exact Host/Origin, token, fetch metadata,
  validation redaction, request/file-count bounds and OpenAPI token declaration.
- Browser inspection at desktop and 390px mobile width. Mobile document width and scrollWidth
  both 390px (no horizontal overflow); image element count 0.
- Browser selected only the committed synthetic JSON and successfully ran parse-only:
  1 parsed / 0 review / no saved record.
- A separate `.cache/ui-fixture` database with synthetic vectors exercised accepted/duplicate/review
  rendering, 0.9500 / 1.0000 score display, filter tabs and correction to an existing accepted pair.
  The correction preserved the review row and reduced unresolved count to 0.
  This UI explicitly labels the synthetic backend as not a Qwen measurement.
- Follow-up local review's four findings were fixed: conflicting JSON representations,
  nonstandard negative labels, stale privacy responses, and overwritten review resolution links.
  `node --test tests/test_ui_privacy.cjs`: **2 passed**, including deferred HTTP response
  inversion and synchronous privacy clearing. Browser recheck found 0 prompt text blocks
  and 0 filename labels after switching text visibility off.
- Three additional parent-assessed Opus findings were fixed: pinning a vector space
  before any vectors exist, CLI reordering of explicit files, and overlooked additional
  conditioning consumers such as FaceDetailer. Regression tests cover dependency changes
  after review-only import, legacy premature pins, failed negative embeddings, existing
  space protection, explicit b/a batch order, mixed directory/file blocks, and extra
  conditioning routes. Shared positive/negative CLIP nodes remain supported; no speculative
  Sentence Transformers whitespace behavior change was made.

The first sandboxed attempt could not use Windows loopback/thread machinery and atomic rename
in its isolated temp area. The authorized native local test run completed all tests, including
junction and atomic replace. No security settings or existing environments were changed.

## Not validated / remaining gates

- **Real Qwen inference has not been run.** Official metadata resolved revision
  `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`; model assets are about 1.21 GB, and the
  CPU dependency download estimate plus model is about 1.43 GB (below the 5 GB budget).
  `torch==2.14.1+cpu`, `sentence-transformers==5.7.0`, `transformers==4.57.6`, and
  `huggingface-hub==0.36.2` were installed successfully in the dedicated environment.
  The explicit official setup-model download failed with **SSLError** before fetching
  any model file. The model directory is empty: no weights and no manifest.
  One subsequently authorized retry with the same library, endpoint, revision and default
  TLS verification confirmed `ssl.SSLCertVerificationError`: `CERTIFICATE_VERIFY_FAILED`,
  `self signed certificate in certificate chain`. It failed during the repository
  metadata request, before weight transfer. No certificate bypass, authentication change
  or alternate downloader was used, and no further requests were attempted.
  No claim is made about real semantic accuracy, latency or CPU RAM at 32K tokens.
  A temporary synthetic-only smoke script is prepared in ignored `.cache/`; execution
  remains blocked by the model download failure. It enforces the requested 6 GiB RAM floor.
- Linux and Python 3.12 CI are configured but have not run in this local Windows session.
- No real image pixels, thumbnails, historical prompts or existing training datasets were opened.
- The metadata-only check for an existing Qwen embedding cache found no candidate in the standard
  Hugging Face cache. This was not a disk-wide model search.
- No packaging of binaries/weights was performed. A future binary distribution requires a full
  bundled-license/NOTICE audit beyond this source-only redistribution notice.
- The parent reports a completed actual Claude Code **Opus 5.5** review of prior source
  tree `8f7ed8f33e72b5d21093e8e839919b4f8e721796`. Its three confirmed follow-up findings
  have been addressed here; this newer revision awaits the parent's final static check.
  This implementation task did not start Claude or send source to Anthropic.
- **No GitHub repo, remote, commit, push or publication.** Latest user instruction is to leave the
  app local for their own functional validation. Publication is explicitly on hold.

## Review entry points

- `README.md`: usage, limits, relative paths and redistribution
- `docs/DESIGN.md`: data flow and invariants
- `prompt_ledger/parsers.py`: metadata parsing and ambiguity handling
- `prompt_ledger/embedding.py`: CPU/offline Qwen and vector-space identity
- `prompt_ledger/core.py`: exact comparison, persistence and atomic export
- `prompt_ledger/config.py`: root/path containment
- `prompt_ledger/web.py`: loopback/token/request-size boundary and API
- `prompt_ledger/cli.py`: machine interface and exit codes
- `prompt_ledger/static/`: Japanese responsive UI without image elements
- `tests/`: synthetic evidence only

Use `git diff --cached` for the complete initial implementation once staged. There is no prior
codebase or commit history in this newly initialized local repository.
# Drag/drop and recursive folder follow-up

Isolated source validation: 161 Python tests passed, 1 skipped; 23 Node UI tests
passed; Ruff and JS syntax checks passed. Synthetic tests cover page-wide file
drops, file-item fallback, unsupported/URL-only drops, busy feedback, 102 entries
across directory-reader batches, sibling subfolders, relative upload names,
cycle/outside/dot/link/error rejection and cancellation during a pending reader.

In a separate loopback IAB browser fixture, the old JS ignored a file dropped
outside the dashed area (no default prevention, no selection). The revised JS
prevented navigation and selected it. A real folder chooser selected both files
under synthetic img/imgfolder1 and img/imgfolder2; metadata-only check parsed 2.
The same chooser omitted synthetic Windows junctions to an outside sibling and
to the folder itself. DOM-dispatched directory-entry drops selected both nested
files and rejected an outside entry while keeping the prior selection.

This is not an Explorer-to-browser native drag test. Other OS/browser link
semantics and real images/GPU were not tested. Existing running user apps/ports
were not restarted or stopped; implementation was completed in an isolated source
copy after the instruction to preserve the running app.
