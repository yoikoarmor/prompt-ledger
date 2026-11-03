"""Windows launcher preflight: no downloads, model loading, or data inspection."""

import argparse
import importlib.util
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prompt Ledger 起動確認")
    parser.add_argument("--check", action="store_true", help="確認のみ。サーバーやDBを起動しない")
    parser.add_argument("--device", choices=("cpu", "cuda", "auto"))
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    if any(importlib.util.find_spec(name) is None for name in ("fastapi", "uvicorn")):
        print("[エラー] 必須の依存が不足しています。このフォルダで uv sync --frozen を実行してください。")
        return 2
    try:
        from prompt_ledger.config import Config
        from prompt_ledger.embedding import QwenEmbedder

        cfg = Config.load(root)
        model_dependencies = all(
            importlib.util.find_spec(name) is not None
            for name in ("sentence_transformers", "transformers", "torch", "huggingface_hub")
        )
        if not model_dependencies:
            print(
                "[案内] モデル用の依存が不足しています。必要なときに uv sync --frozen --extra model を実行してください。"
            )
        # Identity reads only the small manifest; never loads weights or scans inputs/DB.
        from prompt_ledger.config import LedgerError

        try:
            _ = QwenEmbedder(cfg).identity
        except (LedgerError, OSError, ValueError, TypeError, KeyError):
            print(
                "[案内] モデル未取得、またはmanifestが不正です。README.md の Qwen model setup を確認してください。"
            )
            print(
                "固定revisionを指定する setup-model が必要です。重みの自動取得やTLS設定の変更は行いません。"
            )
        if args.check:
            print("起動確認完了。モデルの実推論・重みの完全性は未検証です。")
            return 0
        print("http://127.0.0.1:8765 をブラウザで開いてください。終了は Ctrl+C です。", flush=True)
        print("モデル未準備でも画面と解析のみを利用できます。", flush=True)
        from prompt_ledger.cli import main as cli_main

        device_args = ["--device", args.device] if args.device else []
        return cli_main(["--root", str(root), *device_args, "serve"])
    except KeyboardInterrupt:
        return 130
    except Exception:  # noqa: BLE001 - keep paths and private contents out of launch errors
        print("[エラー] 起動できません。README.md と設定・依存・ポート8765の使用状況を確認してください。")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
