# Agent operating contract

This is a local, metadata-only tool. Use the CLI or documented JSON API.
Do not open or render real images, thumbnails, screenshots of image collections,
or user prompt history. Do not auto-scan existing folders. Do not modify, delete,
copy, upload or commit original images, prompts, databases, embeddings, secrets,
model weights, or unrelated datasets. Work on synthetic fixtures during development.

- Read README.md and docs/DESIGN.md before changing behavior.
- Use project-relative paths in config, source references, examples and generated manifests.
- An explicit runtime --root / LEDGER_ROOT can be absolute, but must not be persisted.
- Use `import ... --check-only` to inspect parseability without prompt output or model loading.
- Use `import ... --dry-run` before writing when appropriate. Dry-run IDs are provisional.
- CLI JSON stdout is the machine interface. Check schema_version and exit code.
- `records` is privacy-preserving by default. Do not opt into text or export unless needed and authorized.
- Qwen defaults to CPU; users may explicitly select cuda or auto. Keep float32,
  offline, local_files_only=True, trust_remote_code=False and device-space isolation.
- Do not modify other virtual environments, unload existing models, or add paid APIs.
- Development tests use synthetic CUDA stubs; do not use the real GPU without separate authorization.
- No external network requests during inference. `setup-model` is an explicit separate download.
- Preserve the conjunctive positive AND negative >= threshold rule. No ANN-only acceptance.
- Apply saved literal exclusion rules before embedding and at every export through the common core.
- Rule edits use optimistic versions; private responses must not expose terms or prompts.
- Never silently truncate text; reject invalid or incompatible vectors/model spaces.
- Same batch must see earlier accepted entries. Keep transaction serialization and atomic export.
- New parsers must treat metadata as data, never execute code, and send ambiguous inputs to review.
- Do not publish or push until the parent's designated prepublication review is completed.

Useful commands (from project root):

```sh
uv run --frozen --extra dev pytest -q
uv run --frozen --extra dev ruff check prompt_ledger tests
uv run --frozen prompt-ledger --root . status
uv run --frozen prompt-ledger --root . import selected/example.json --check-only
```

Use only dedicated `.venv`. Do not commit it. Keep dependencies locked in uv.lock.
GitHub CI runs only synthetic tests. No images/model download tokens are required.
