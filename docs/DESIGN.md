# Design: one core, three interfaces

The optional staged path reads a bounded extraction envelope, prepares at most
one small vector batch, and invokes the same Ledger._add decision inside bounded
transactions. Completed batches persist for idempotent replay; only the active
batch rolls back on cancellation. An atomic streamed comparison export contains
the accepted collection plus a text-free decision audit from one read snapshot.
JSON uploads and job progress remain behind the existing local token boundary.
See STAGED_PIPELINE_JA.md for space compatibility, cancellation and performance limits.

## Data flow

User-selected files → bounded parser registry → explicit positive/negative pair →
current literal exclusion check → exact pair hash lookup → offline Qwen embeddings → exhaustive cosine comparisons →
accepted / duplicate / review records → accepted-only JSON export.

The browser, API and CLI all enter the Ledger core. They do not implement their own
similarity rules. Parsing is independent, so check-only works without a model.
No file is read based on an API-supplied disk path. Browser data streams one file at a time through the metadata sieve; the legacy
base64 API keeps a bounded JSON envelope. CLI files stay in the configured input root.
Image bytes exist only transiently in memory. PNG compressed pixels are skipped,
JPEG traversal stops before scan data. Neither path decodes pixels.

## Storage and invariants

SQLite `meta` pins schema and vector-space/decision configuration. `records` stores
pair strings, normalized vectors, hashes, relative source, status, reason, nearest
accepted ID, two scores, timestamp, and optional review resolution link.
There is no image blob column. Input pair hashes preserve exact Unicode content.
Review hashes are source+input bytes because a failed parse has no pair to hash.
No vector is generated for an empty negative; the comparison uses the explicit
empty rules. Whitespace-only negative is preserved as text, not silently emptied.
The vector-space identity is pinned only after both required embeddings pass validation,
inside the same write transaction as the first saved vectors. Metadata-only reviews and
token-limit reviews do not pin it; a failed negative embedding discards the partial positive
vector. A legacy premature pin can be cleared only when no vectors or accepted entries
exist. Existing vector spaces remain protected against model/settings changes.

`BEGIN IMMEDIATE` serializes writes including comparison and model calls. This is
deliberately conservative: a concurrent transaction cannot accept a duplicate from
stale state. Batch processing order is supplied order (CLI directory files sort by
relative path; UI uses selected-file order). Tie-breaking uses lowest accepted ID.
CLI sorting applies independently to each directory expansion at its argument position;
explicit filenames and the ordering between argument blocks are never sorted.
Exact repeated pairs reuse an existing row even if filename or pixel data changes.
This is a prompt ledger, not a one-record-per-image asset catalog.

Review correction writes/reuses the corrected pair and links the original row to
the resulting accepted/duplicate entry. It does not overwrite raw history. If the
correction still fails token limits, the original remains unresolved.
Once resolved, further corrections to the same review return a conflict rather
than replacing its historical resolution link.
Export streams one row at a time in one read snapshot and includes only accepted records. Atomic file
replacement keeps an older export intact if replacement fails.

## Exclusion rules

Versioned rule lists and exclusion audit links live in the same relative SQLite
store. Rules are literal substrings under Unicode casefold, tested separately
against positive and negative before embedding. Rule edits and imports serialize;
optimistic version checks prevent lost edits. Existing accepted rows retain their
stored state but acquire an effective excluded status while a current rule matches.
Every export filters using its transaction's rules snapshot, including previously
accepted rows. Initial exclusions save no vectors; after rule removal an explicit
reimport is required to create a normal pair record, preserving exclusion history.
Default reads expose only generic reasons, counts and versions. Rule text and
matching terms require explicit text mode. See EXCLUSIONS.md for the complete contract.

## UI

Japanese, quiet warm paper background, green accents, responsive two-column layout.
Top: purpose, threshold and precise AND rule. Counts distinguish accepted,
duplicates and unresolved review. Left: explicit file/folder selection, progress, cancellation, dry-run,
parse-only and import. Right: privacy-first records with filter tabs, paired scores,
nearest IDs, review correction and deliberate export. No image previews, remote
fonts, analytics, third-party scripts, or remote assets. DOM text uses textContent,
never unsanitized HTML. File input and live status remain keyboard accessible.
Privacy-off immediately removes cached text/source and their DOM elements. Refresh
generation IDs discard stale HTTP responses, and source labels also require the
current explicit text-visibility setting.

## Trust and limits

HTTP bind is exactly 127.0.0.1; exact Host and Origin checks resist DNS rebinding.
Fetch metadata blocks cross-site calls; ephemeral token protects all `/api/*`.
The token is obtained from same-origin `/session`; no local-user isolation is claimed.
Legacy JSON requests retain a transport budget before JSON decoding. The raw
`/api/file` path streams image data through a bounded metadata sieve with no
file-size cap. CLI reads 64 KiB chunks; supported metadata is retained and image
payload is discarded, never copied to disk. Counts are unrestricted; result
details are capped for display only. Explicit folders are walked without links.
Browser normal imports commit per file; cancellation preserves completed work.
Browser dry-run uses one bounded worker queue and a rollback-only transaction
with an inactivity timeout, so earlier provisional accepts remain visible.
CLI batches retain all-or-nothing transaction/rollback behavior.
Compressed metadata decompression is bounded; no ZIP processing exists.
Malformed or unsupported inputs fail closed to review. This application is intended
for a trusted local user, not a hostile multiuser machine or public web server.
Concurrent adversarial filesystem mutation after path checks is outside that local
trust boundary; do not make data/model/input directories writable to untrusted users.

## Deliberately deferred

CPU is default; explicit cuda fails when unavailable, while auto selects CPU only
if CUDA is unavailable before loading. Runtime device is part of vector-space
identity; no silent fallback occurs after CUDA OOM or other inference failure.
Requested/selected device and loaded state are exposed without prompt text.
CUDA dependencies are locked from the official cu130 index, mutually exclusive
with the CPU extra. Development validation uses stubs, not competing GPU workloads.

Windows setup downloads the pinned official Qwen snapshot by default. Acquisition
is separate from inference: official file sizes and LFS SHA-256/Git blob hashes
are recorded in a relative-path resume plan, verified files are reused, and a
completed SHA-256 manifest is atomically published. An exclusive setup lock
prevents concurrent download writers. Unknown files and other revisions fail
closed; TLS failures never enable an insecure fallback. Setup does not load models.
Each setup invocation must fetch fresh metadata for the exact official revision.
Local manifest hashes and resume plans are not authenticity authorities. Empty or
malformed resume inventories fail closed before any successful manifest is written.
Existing model-cache entries are inspected with lstat before any setup write and
again before each Hub download. The traversal rejects symbolic links and Windows
reparse points before descending, including nested metadata/lock/partial entries.
It never repairs, deletes or follows an unsafe cache link. Concurrent adversarial
filesystem replacement after these checks remains outside the local trust boundary.

No image similarity, generation, automatic folder watch, hidden history ingestion,
ANN search, model-space migration, joint embedding mode, archive importer, or cloud
hosting. No automatic threshold tuning. Qwen semantic quality must be evaluated
on a user-approved sample locally before relying on the dedup decisions.


Explicit metadata extraction bypasses embeddings and deduplication. Shared text_export.metadata_record applies exclusions before returning text. CLI uses one rules snapshot and atomic streamed output; UI uses raw per-file API with rule-version consistency checks. Extraction does not insert records or vectors. See EXTRACTION_COMPARISON_JA.md.


Nearest comparison is implemented in comparison.py: 128-row bounded blocks, CPU NumPy or explicit CUDA float64 matrix-vector products, exact fsum reranking within a conservative rounding margin. CUDA comparison failure uses the same vectors on CPU and reports fallback; embedding CUDA errors do not change model space. CUDA pair embedding batches up to two texts within a 4096 padded-token budget, with a pinned CUDA batching identity. See EXTRACTION_COMPARISON_JA.md.
