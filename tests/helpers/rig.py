"""A real Engine over a real home in tmp, no threads, a clock that only moves when told: shared by the engine tests."""

from __future__ import annotations

import contextlib
import io
import json
import tarfile
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from phone import runner
from phone.entry import Engine
from tests.helpers.fakes import FakeClock, loopback_gate_options

GH = "ghp_STATUSrig0123456789abcdefABCDEFGH"
LAN = "link-token-status-0123456789abcdef"
T0 = datetime(2026, 10, 12, 10, 0, tzinfo=timezone.utc).timestamp()
WIFI = {"battery_pct": 82, "charging": True, "temp_c": 33.0, "network": "wifi", "wifi_ip": "192.168.1.3",
        "data_saver": False}
ISO = "%Y-%m-%dT%H:%M:%SZ"
LAYERS = ["L1", "L0", "L2", "L3g", "L3c", "L4", "LK"]


class Alive:
    def is_alive(self) -> bool:
        return True

    def join(self, timeout=None) -> None:
        return None


class Rig:
    def __init__(self, tmp_path: Path, *, tokens: bool = True, candidates: int | None = 100, facts: dict | None = WIFI,
                 accepted: bool = True, started: bool = True) -> None:
        self.clock = FakeClock(T0)
        self.free = [150.0]
        self.eng = Engine(tmp_path / "files", clock=self.clock, free_gb=lambda: self.free[0], threads=False,
                          gather_main=lambda argv: 0, gate_options=loopback_gate_options(), lan_port=0)
        self.home = self.eng.home
        if candidates is not None:
            self.candidates(candidates)
        if started:
            self.eng.start(GH if tokens else None, LAN if tokens else None)
            if tokens and accepted:
                self.eng.controls.set_token(True, "accepted", self.clock())
        if facts is not None:
            self.eng.update_device(json.dumps(facts))
        self.eng._collector = Alive()

    def candidates(self, n: int) -> None:
        raw = self.home / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "candidates.jsonl").write_text(
            "".join(json.dumps({"owner": f"o{i}", "name": f"n{i}", "ecosystem": "pypi"}) + "\n" for i in range(n)),
            encoding="utf-8")

    def envelopes(self, collector: str, start: int, stop: int) -> None:
        folder = self.home / "raw" / collector
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(start, stop):
            (folder / f"o{i}__n{i}.json").write_text("{}", encoding="utf-8")

    def mark(self, name: str, ts: float | str) -> None:
        text = ts if isinstance(ts, str) else datetime.fromtimestamp(ts, timezone.utc).strftime(ISO)
        path = self.home / "state" / "refresh" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")

    def run_on(self, layer: str = "L1", run: int = 1) -> None:
        rs = self.eng.runstate
        rs.mode, rs.layer, rs.run = "running", layer, run
        rs.collectors = tuple(dict(runner.MAIN_LAYERS + [runner.KEYLESS_LAYER])[layer].split(","))
        self.eng.progress.tick(layer, rs.run, rs.checked, rs.total, active=True, force=True)

    def status(self) -> dict:
        return json.loads(self.eng.status())

    def at(self, hhmm_after_seconds: float) -> str:
        return datetime.fromtimestamp(self.clock() + hhmm_after_seconds, timezone.utc).strftime("%H:%M")


def idle_rig(tmp_path: Path, main_done: float = datetime(2026, 10, 11, 4, 4, 6, tzinfo=timezone.utc).timestamp()):
    """A home with every step complete and a fresh MAIN.done, after one real pass of ``run_forever`` into idle."""
    r = Rig(tmp_path)
    for layer in LAYERS:
        r.mark(f"{layer}.complete", main_done)
    r.mark("MAIN.done", main_done)
    lines: list[str] = []

    class Stop(BaseException):
        pass

    def sleep(_s):
        raise Stop

    with contextlib.suppress(Stop):
        runner.run_forever(r.home, lambda argv: 0, every_days=lambda: r.eng.settings.value("interval_days"),
                           sleep=sleep, now=r.clock, log=lambda line: (lines.append(line), r.eng._runner_log(line)),
                           layers=lambda: r.eng.settings.value("layers"), state=r.eng.runstate,
                           notify=r.eng._notify, poll_s=30)
    return r, lines


def leaks(root: Path, needles: list[str]) -> list[str]:
    """Files under ``root`` (and members of any .tar.gz inside them) whose bytes contain any needle."""
    wanted = {n.encode() for n in needles} | {urllib.parse.quote(n, safe="").encode() for n in needles}

    def has(data: bytes) -> bool:
        return any(w in data for w in wanted)

    hits: list[str] = []
    for path in sorted(Path(root).rglob("*")):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if has(data):
            hits.append(str(path))
        if path.name.endswith(".tar.gz"):
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                for member in tar.getmembers():
                    body = tar.extractfile(member)
                    if body is not None and has(body.read()):      # read once: a second read() would be empty
                        hits.append(f"{path}:{member.name}")
    return hits
