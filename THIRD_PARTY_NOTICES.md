# Third-party software and model notices

Prompt Ledger's original source is MIT licensed; this does not relicense its
dependencies, model weights, or user-provided data. This repository distributes
source and a dependency lock, not dependency binaries or Qwen weights.

Direct runtime dependencies:

| Project | License | Upstream |
| --- | --- | --- |
| FastAPI | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | BSD-3-Clause | https://github.com/encode/uvicorn |
| Sentence Transformers (optional model extra) | Apache-2.0 | https://github.com/huggingface/sentence-transformers |
| Transformers (optional model extra) | Apache-2.0 | https://github.com/huggingface/transformers |
| Hugging Face Hub (optional model extra) | Apache-2.0 | https://github.com/huggingface/huggingface_hub |
| PyTorch CPU (optional model extra) | BSD-style, additional bundled notices | https://github.com/pytorch/pytorch/blob/main/LICENSE |

`uv.lock` records exact resolved versions, transitive dependencies, registry URLs,
and artifact hashes. Development dependencies include pytest (MIT), httpx
(BSD-3-Clause), Ruff (MIT), and the Hatchling build backend (MIT).
Transitive NumPy/SciPy and native math libraries have their own notices.
If you later distribute a packaged executable, container, environment, or model,
collect and retain the license/NOTICE files of **all components actually bundled**.
This source-only notice is not a complete binary-distribution license inventory.

The separately downloaded model is **Qwen/Qwen3-Embedding-0.6B**.
Its official model card identifies Apache-2.0, a 32K context and up to 1024 dimensions:
https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
https://qwenlm.github.io/blog/qwen3-embedding/
The model card is the source of this license identification; no claim is made
that a standalone LICENSE exists at the model repository root.
Weights are not included or downloaded automatically. The explicit setup command
fetches an immutable official snapshot, and retains any LICENSE/NOTICE files in
that snapshot. Review upstream terms before redistributing downloaded artifacts.

No fonts, icons, stock imagery, CDN scripts or image assets are bundled.
The UI uses system fonts, CSS, and text symbols. All committed examples are
synthetic, harmless prompts, authored for this project.
