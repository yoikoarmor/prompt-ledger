"""Explicit Windows setup includes the official pinned model by default."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from prompt_ledger.model_setup import DEFAULT_REVISION

UV_VERSION = "0.11.7"


def run(command, root, env=None):
    subprocess.run(command, cwd=root, env=env, check=True)


def setup(root, check=False, model_revision=DEFAULT_REVISION, cuda=False):
    if not (3, 10) <= sys.version_info[:2] < (3, 14):
        print("[エラー] Python 3.10～3.13が必要です。既存環境は削除しません。")
        return 2
    for name in (".venv", ".cache", ".cache/setup-tools"):
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            print("[エラー] 環境またはキャッシュが外部を参照しています。設定を確認してください。")
            return 2
    if check:
        print("Python対応バージョン確認済み。ダウンロード・環境変更は行いません。")
        return 0
    python = root / ".venv/Scripts/python.exe"
    if not python.exists():
        if (root / ".venv").exists():
            print("[エラー] 既存 .venv が不完全です。自動削除しません。README.mdを確認してください。")
            return 2
        run([sys.executable, "-m", "venv", str(root / ".venv")], root)
    # uv itself is pinned and kept outside site-packages so sync cannot remove its running executable.
    tools = root / ".cache/setup-tools"
    env = {
        **os.environ,
        "PYTHONPATH": str(tools),
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_PROJECT_ENVIRONMENT": str(root / ".venv"),
    }
    probe = subprocess.run(
        [str(python), "-m", "uv", "--version"], cwd=root, env=env, capture_output=True, text=True, check=False
    )
    if probe.returncode or not probe.stdout.startswith("uv " + UV_VERSION + " "):
        print("公式PyPIから固定版uvを取得します。通信が発生します。", flush=True)
        run([str(python), "-m", "ensurepip"], root)
        run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--isolated",
                "--index-url",
                "https://pypi.org/simple",
                "--target",
                str(tools),
                "--upgrade",
                "--no-deps",
                "uv==" + UV_VERSION,
            ],
            root,
        )
    command = [
        str(python),
        "-m",
        "uv",
        "sync",
        "--frozen",
        "--inexact",
        "--no-python-downloads",
        "--python",
        str(python),
    ]
    if model_revision:
        command.extend(["--extra", "cuda" if cuda else "model"])
    print("uv.lockの固定依存を公式配布元から導入します。既存データは削除しません。", flush=True)
    run(command, root, env)
    if model_revision:
        run(
            [
                str(python),
                "-X",
                "utf8",
                "-m",
                "prompt_ledger.cli",
                "--root",
                str(root),
                "setup-model",
                "--revision",
                model_revision,
            ],
            root,
        )
    print("準備完了。start.bat を実行してください。このセットアップでは推論は実行していません。")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Prompt Ledger Windows setup")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--model-revision", default=DEFAULT_REVISION, help="公式Qwenの40桁commit SHA")
    parser.add_argument("--skip-model", action="store_true", help="明示的に基本依存のみ導入")
    parser.add_argument(
        "--cuda", action="store_true", help="CUDA版PyTorchを導入。実推論やドライバ変更はしない"
    )
    args = parser.parse_args(argv)
    if args.cuda and args.skip_model:
        print("[エラー] --cuda と --skip-model は同時に指定できません。")
        return 2
    if args.skip_model:
        args.model_revision = None
    if args.model_revision:
        import re

        if not re.fullmatch("[0-9a-f]{40}", args.model_revision):
            print("[エラー] revisionは公式モデルの40桁commit SHAを指定してください。")
            return 2
        print("モデル取得を選択しました。重み約1.2GB＋CPU依存など数GBの通信・空き容量が必要です。")
        print("公式PyPI/PyTorch/Hugging Faceを使用します。TLS検証は無効化しません。")
        if args.cuda:
            print("CUDA 13.0版を導入します。追加の大きな通信・ディスク使用が発生します。")
            print(
                "起動は start.bat --device cuda。CUDA不能時にCPUを選ぶ場合は --device auto。ドライバ変更は行いません。"
            )
    else:
        print("今回は基本依存のみ。Qwen重みは取得しません。任意取得の手順はREADME.mdにあります。")
    try:
        return setup(Path(__file__).resolve().parent, args.check, args.model_revision, cuda=args.cuda)
    except (OSError, subprocess.CalledProcessError):
        print("[エラー] 導入に失敗しました。上のエラーを確認してください。終了コード1。")
        print("証明書エラーは回避せず、日時・正規の証明書チェーンを管理者に確認してください。")
        print(
            "既存環境/データは削除していません。原因解消後に再実行できます。モデル途中取得はREADMEを確認してください。"
        )
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
