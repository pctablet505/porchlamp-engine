"""The Termux fallback scripts: the LAN server binds loopback unless told otherwise, and the token is placed on the phone only."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

TERMUX = Path(__file__).resolve().parents[2] / "phone" / "termux"


def boot_with_fake_tools(tmp_path: Path, extra_env: dict[str, str]) -> str:
    """Run boot.sh with a fake tmux that prints the command it was asked to start; return what it printed."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "tmux").write_text('#!/bin/sh\n[ "$1" = has-session ] && exit 1\necho "TMUX $*"\n')
    (bin_dir / "termux-wake-lock").write_text("#!/bin/sh\nexit 0\n")
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    home = tmp_path / "home"
    (home / "state" / "refresh").mkdir(parents=True)
    (home / "state" / "refresh" / "MAIN.done").write_text("2026-10-11T04:04:06Z\n")
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "PORCHLAMP_PHONE_HOME": str(home),
           "PORCHLAMP_ENGINE": str(tmp_path / "engine"), **extra_env}
    done = subprocess.run(["bash", str(TERMUX / "boot.sh")], env=env, capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_boot_starts_the_server_on_loopback_by_default_and_never_on_all_addresses(tmp_path):
    out = boot_with_fake_tools(tmp_path, {})
    command = next(line for line in out.splitlines() if "phone.serve" in line)
    assert re.search(r"--bind '127\.0\.0\.1'", command) and "0.0.0.0" not in command
    assert "0.0.0.0" not in (TERMUX / "boot.sh").read_text().replace("never 0.0.0.0", "")


def test_boot_binds_only_the_address_the_owner_names(tmp_path):
    out = boot_with_fake_tools(tmp_path, {"PORCHLAMP_SERVE_BIND": "192.168.1.3"})
    assert "--bind '192.168.1.3'" in out


def test_setup_and_refresh_say_the_token_is_placed_on_the_phone_only():
    setup = (TERMUX / "setup.sh").read_text()
    assert "placed on the phone only" in setup and "NO scopes" in setup
    refresh = (TERMUX / "refresh_phone.sh").read_text()
    assert "placed on the phone only" in refresh
    assert not re.search(r"echo[^\n]*\$GH_TOKEN|printf[^\n]*\$GH_TOKEN", refresh)       # the value is never printed
    assert os.access(TERMUX / "boot.sh", os.X_OK)
