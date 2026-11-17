"""The Raspberry Pi installer: valid shell, secrets kept out of the unit."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "install" / "raspberry-pi.sh"


def test_it_is_valid_shell() -> None:
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("No sh here.")
    checked = subprocess.run([sh, "-n", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert checked.returncode == 0, checked.stderr


def test_keys_live_in_a_private_file_not_in_the_service() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "set -eu" in text
    assert "umask 077" in text
    unit = text[text.index("[Service]") : text.index("[Install]")]
    assert "EnvironmentFile=$ENV_FILE" in unit
    assert "--serve" in unit
    assert "KEY" not in unit  # keys come from the environment file only
    assert "somame-core==$VERSION" in text  # a pinned release, not whatever is newest
