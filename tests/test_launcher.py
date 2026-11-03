import importlib.util
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ledger_launcher", ROOT / "launch.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_launcher_uses_script_root_and_loopback_cli(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LEDGER_ROOT", str(tmp_path / "wrong"))
    with patch("prompt_ledger.cli.main", return_value=0) as cli:
        assert launcher.main([]) == 0
    cli.assert_called_once_with(["--root", str(ROOT), "serve"])


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch integration")
def test_batch_missing_environment_dependencies_and_model(tmp_path):
    root = tmp_path / "日本語 launch space"
    root.mkdir()
    shutil.copyfile(ROOT / "start.bat", root / "start.bat")
    shutil.copyfile(ROOT / "launch.py", root / "launch.py")
    (root / "docs").mkdir()
    shutil.copyfile(ROOT / "docs/START_WINDOWS.txt", root / "docs/START_WINDOWS.txt")
    shutil.copytree(
        ROOT / "prompt_ledger", root / "prompt_ledger", ignore=shutil.ignore_patterns("__pycache__")
    )

    def check():
        return subprocess.run(
            ["cmd.exe", "/d", "/c", str(root / "start.bat"), "--check"],
            cwd=tmp_path,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            env={**os.environ, "LEDGER_ROOT": str(tmp_path / "wrong")},
        )

    result = check()
    assert result.returncode == 2 and ".venv がありません" in result.stdout
    venv.EnvBuilder(with_pip=False).create(root / ".venv")
    result = check()
    assert result.returncode == 2 and "依存が不足" in result.stdout
    # Reuse this project's installed dependencies without downloads or other environment edits.
    site_dir = Path(sys.prefix) / "Lib" / "site-packages"
    (root / ".venv/Lib/site-packages/test_dependencies.pth").write_text(str(site_dir), encoding="utf-8")
    result = check()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "モデル未取得" in result.stdout
    assert "起動確認完了" in result.stdout
    assert not (root / "data").exists()
    assert not (root / "models").exists()
