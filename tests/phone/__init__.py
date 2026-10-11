"""PHONE-1 tests, and the shared fake phone home they build on."""

from __future__ import annotations

import json
from pathlib import Path

from phone.archive import LAYERS

DUMMY_TOKEN = "t" * 32
MARKER_TEXT = "2026-10-10T12:00:00Z"


def make_home(root: Path, layers=("L1", "L0", "L2"), files_per: int = 50, token: str | None = DUMMY_TOKEN) -> Path:
    """A fake phone home: raw envelopes and a ``.complete`` marker for each layer in ``layers``."""
    home = Path(root)
    for layer in layers:
        for collector in LAYERS[layer]:
            folder = home / "raw" / collector
            folder.mkdir(parents=True, exist_ok=True)
            for i in range(files_per):
                body = {"collector": collector, "repo": f"org/{collector}-{i:04d}", "value": i * 7}
                (folder / f"{collector}-{i:04d}.json").write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
        marker = home / "state" / "refresh" / f"{layer}.complete"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(MARKER_TEXT + "\n", encoding="utf-8")
    if token is not None:
        (home / "config").mkdir(parents=True, exist_ok=True)
        (home / "config" / "lan_token").write_text(token + "\n", encoding="utf-8")
    return home
