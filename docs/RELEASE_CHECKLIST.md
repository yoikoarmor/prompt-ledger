# Prepublication checklist

- Run the synthetic test suite, Ruff and JavaScript syntax check.
- Review UI at desktop and mobile widths using synthetic data only.
- Verify `git ls-files` and staged diff contain only source, docs, lock and synthetic examples.
- Exclude input/, data/, outputs/, models/, .venv/, .cache/, logs, .env and real prompts.
- Scan tracked files for private absolute roots, credentials and access tokens.
- Verify config paths and persisted source refs are relative; test another CWD and Unicode/spaces root.
- Review the MIT license, upstream notices and source-only redistribution scope.
- Record real Qwen test status separately from fake mathematical vector tests.
- Confirm dependency/model licenses from primary upstream sources before bundling any binaries.
- Parent coordinates the user-requested actual Claude Code Opus 5.5 review, with
  source/synthetic evidence only. Record actual model ID and review outcome.
- Do not claim that review occurred if the provider/model is unavailable or blocked.
- Do not create a GitHub repo or push before the parent's release gate is satisfied.

No publishing action is part of this implementation handoff.
