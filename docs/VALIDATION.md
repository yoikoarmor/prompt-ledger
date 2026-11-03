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
