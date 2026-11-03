# Design: one core, three interfaces

## Data flow

User-selected files → bounded parser registry → explicit positive/negative pair →
current literal exclusion check → exact pair hash lookup → offline Qwen embeddings → exhaustive cosine comparisons →
accepted / duplicate / review records → accepted-only JSON export.

The browser, API and CLI all enter `Ledger.ingest`. They do not implement their own
similarity rules. Parsing is independent, so check-only works without a model.
No file is read based on an API-supplied disk path. Browser data is base64 in a
bounded JSON request; CLI files must remain within the configured input root.
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
Export reads in one transaction and includes only accepted records. Atomic file
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
duplicates and unresolved review. Left: explicit user selection, limits, dry-run,
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
Body streaming is bounded before JSON decoding, then decoded file limits apply.
Compressed metadata decompression is bounded; no ZIP processing exists.
Malformed or unsupported inputs fail closed to review. This application is intended
for a trusted local user, not a hostile multiuser machine or public web server.
Concurrent adversarial filesystem mutation after path checks is outside that local
trust boundary; do not make data/model/input directories writable to untrusted users.

## Deliberately deferred

No image similarity, generation, automatic folder watch, hidden history ingestion,
ANN search, model-space migration, joint embedding mode, archive importer, or cloud
hosting. No automatic threshold tuning. Qwen semantic quality must be evaluated
on a user-approved sample locally before relying on the dedup decisions.
