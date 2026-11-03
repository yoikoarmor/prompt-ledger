# Literal exclusion rules

The default list is empty. Up to 100 nonblank single-line strings, each at most
256 characters, can be saved. Matching is literal substring matching against
positive OR negative, using Python Unicode `casefold()` on both sides. It is
case-insensitive (including Unicode folding such as Straße/STRASSE), not regex,
not whole-word matching, and does not normalize accents, width or whitespace.
Whitespace inside a nonblank rule is significant. Equivalent casefolded duplicates
are deduplicated. No supplied rule or prompt is executed.

UI: enable **本文を表示**, edit **除外する文字列** (one per line), then save.
Switching visibility off immediately clears the rule editor and matched terms;
stale asynchronous text/rule responses are ignored. Import/review responses,
default records, status and default rule reads never return the terms or prompts.
Explicit text mode reveals current matches and the original exclusion rule version.

CLI (all commands use the same root and core):

```sh
prompt-ledger --root . rules
prompt-ledger --root . rules --include-text
prompt-ledger --root . rules --set-json '["watermark","synthetic example"]' --expected-version 0
prompt-ledger --root . rules --set-json '[]' --expected-version 1
```

Use the current version from `rules`; edits with an outdated version fail rather
than overwrite a concurrent edit. API: GET `/api/rules?privacy=true`, explicit
`privacy=false` for terms, PUT `/api/rules` with `terms` and `expected_version`.
The existing session token and exact loopback boundary apply to these routes too.
Rule conflicts return HTTP 409. Updates return only count/version, not rule text.

Rules and immutable rule versions are stored in the existing root-relative SQLite
database, not in the source config. Each newly excluded pair keeps its original
rule version in an audit table; source bytes are not stored. Excluded pairs are
checked before model identity/loading/encoding and can be imported without Qwen.
Malformed metadata remains review because a complete pair cannot safely be inferred.
`--check-only` remains parse-only: it does not judge exclusions, embed, or export.

Current rules are also applied to existing records and every export snapshot.
Stored accepted rows and their vectors are preserved: they appear excluded while
a current rule matches, and become exportable again when that rule is removed.
Currently excluded accepted rows are not similarity candidates for new imports.
Rules do not change vector-space identity or reclassify historical duplicates.
Imports and edits serialize via SQLite; export reads the rules and rows from one
transaction snapshot. Already downloaded/exported files cannot be recalled or
retroactively edited; perform a fresh export after editing rules.

Pairs excluded at initial import have no embeddings and remain historical excluded
records if a rule is removed. Explicitly reimport the original file to run semantic
deduplication then; this creates/reuses a normal pair record while keeping the
exclusion history. Review corrections pass through the same exclusion check, and
an excluded correction never enters accepted export. Dry-run rolls back both the
record and its exclusion event. No API/CLI option disables this export filter.

An excluded correction leaves its original review unresolved. After removing the
rule, use that review's correction action again (especially when the original file
has unparseable metadata). Only a different stored accepted/duplicate record can
resolve a review; an effectively excluded review cannot resolve itself or another
review. Excluded reviews remain accessible in the review tab. Earlier excluded
correction records remain as history, not automatic candidates for promotion.

Removing rules does not erase their immutable audit versions. Restoring previously
accepted rows may expose similar pairs accepted while those rows were excluded;
rule edits do not rerun deduplication across the whole ledger.
