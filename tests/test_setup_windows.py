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
spec = importlib.util.spec_from_file_location("ledger_setup", ROOT / "setup_windows.py")
setup_helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup_helper)


def test_setup_repeated_preserves_data_and_uses_locked_deps(tmp_path):
    root = tmp_path / "日本語 setup space"
    root.mkdir()
    marker = root / "synthetic.txt"
    marker.write_text("keep")
    commands = []

    def run(command, cwd, env=None):
        assert cwd == root
        commands.append(command)
        if "venv" in command:
            python = root / ".venv/Scripts/python.exe"
            python.parent.mkdir(parents=True)
            python.touch()

    with (
        patch.object(setup_helper, "run", side_effect=run),
        patch.object(
            setup_helper.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 0, "uv 0.11.7 (test)"),
        ),
    ):
        assert setup_helper.setup(root) == 0
        assert setup_helper.setup(root) == 0
    assert sum("venv" in command for command in commands) == 1
    assert all("--frozen" in command and "--inexact" in command for command in commands[1:])
    assert all("setup-model" not in command for command in commands)
    assert marker.read_text() == "keep"


def test_setup_failure_and_check_do_not_download(tmp_path):
    with patch.object(setup_helper, "run") as run:
        assert setup_helper.setup(tmp_path, check=True) == 0
        run.assert_not_called()
    with patch.object(setup_helper, "setup", side_effect=subprocess.CalledProcessError(1, ["synthetic"])):
        assert setup_helper.main([]) == 1
    assert setup_helper.main(["--model-revision", "invalid"]) == 2
    with patch.object(setup_helper.sys, "version_info", (3, 9)):
        assert setup_helper.setup(tmp_path) == 2


def test_setup_bootstrap_and_model_are_explicit(tmp_path):
    python = tmp_path / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    with (
        patch.object(setup_helper, "run") as run,
        patch.object(setup_helper.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "")),
    ):
        assert setup_helper.setup(tmp_path, model_revision="a" * 40) == 0
    commands = [call.args[0] for call in run.call_args_list]
    assert "ensurepip" in commands[0]
    assert "uv==0.11.7" in commands[1] and "https://pypi.org/simple" in commands[1]
    assert "--extra" in commands[2] and "model" in commands[2]
    assert commands[3][-3:] == ["setup-model", "--revision", "a" * 40]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows batch")
def test_setup_batch_no_python(tmp_path):
    root = tmp_path / "日本語 setup space"
    (root / "docs").mkdir(parents=True)
    shutil.copyfile(ROOT / "setup.bat", root / "setup.bat")
    shutil.copyfile(ROOT / "docs/SETUP_WINDOWS.txt", root / "docs/SETUP_WINDOWS.txt")
    result = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/c", str(root / "setup.bat"), "--check"],
        cwd=tmp_path,
        env={**os.environ, "PATH": ""},
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 2
    shutil.copyfile(ROOT / "setup_windows.py", root / "setup_windows.py")
    venv.EnvBuilder(with_pip=False).create(root / ".venv")
    result = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/c", str(root / "setup.bat"), "--check"],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0
    assert not (root / ".cache").exists()
