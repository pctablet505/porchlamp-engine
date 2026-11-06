#!/usr/bin/env python3
"""Pull finished refresh layers from the phone's LAN server (phone/serve.py) into a workstation data root.

    python scripts/pull_from_phone.py --url http://HOST:8765 --token-file F --dest DATA_ROOT [--layers L1,L0]
    python scripts/pull_from_phone.py --url http://HOST:8765 --token-file F --push-candidates FILE

Exit codes: 0 ok, 1 usage or HTTP/network error, 3 sha256/size mismatch (nothing extracted),
4 unsafe archive member (nothing extracted), 5 a set-aside target already exists (nothing moved).
Every request is signed with the token (the token is never sent); the manifest is used only after its X-Porch-Mac
reply signature verifies. The token is read from --token-file and never printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from urllib import error, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phone.archive import LAYERS, MAIN_LAYERS
from phone.auth import MAC_HEADER, NONCE_HEADER, sign_request, verify_response

CHUNK = 1 << 20
TIMEOUT_S = 60
CANDIDATES_CAP = 8 * 1024 * 1024


class PullError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _log(message: str) -> None:
    print(message, flush=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _NoRedirect(request.HTTPRedirectHandler):
    """The phone never redirects; a signed request must not be forwarded anywhere else."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = request.build_opener(_NoRedirect)


def _open(url: str, token: str, method: str = "GET", headers=None, data: bytes | None = None):
    """Send one signed request; returns ``(response, nonce)``."""
    req = request.Request(url, method=method, data=data)
    signed = sign_request(token, method, req.selector, data or b"")
    for key, value in {**signed, **(headers or {})}.items():
        req.add_header(key, value)
    try:
        return _OPENER.open(req, timeout=TIMEOUT_S), signed[NONCE_HEADER]
    except error.HTTPError as exc:
        raise PullError(1, f"{method} {url.split('/', 3)[-1]} failed: HTTP {exc.code}") from None
    except (error.URLError, OSError) as exc:
        raise PullError(1, f"{method} {url.split('/', 3)[-1]} failed: {type(exc).__name__}") from None


def _request(url: str, token: str, method: str = "GET", headers=None, data: bytes | None = None):
    return _open(url, token, method, headers, data)[0]


def _get_json(url: str, token: str):
    """GET a JSON reply and parse it only after its ``X-Porch-Mac`` verifies; otherwise exit 1."""
    max_json_bytes = 16 * 1024 * 1024
    resp, nonce = _open(url, token)
    with resp:
        raw = resp.read(max_json_bytes + 1)
        mac = resp.headers.get(MAC_HEADER)
        status = resp.status
    if len(raw) > max_json_bytes:
        raise PullError(1, f"GET {url.split('/', 3)[-1]}: reply exceeds {max_json_bytes} bytes")
    if not verify_response(token, nonce, status, raw, mac):
        raise PullError(1, f"GET {url.split('/', 3)[-1]}: reply signature missing or wrong; nothing used")
    return json.loads(raw.decode("utf-8"))


def _download(base: str, token: str, layer: str, entry: dict, part: Path) -> int | None:
    """Fetch the archive into ``part``, resuming a partial file. Returns the resumed-from offset, or None."""
    part.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    if have > entry["bytes"]:
        part.unlink()
        have = 0
    if have == entry["bytes"] and have > 0:
        return have
    headers = {"Range": f"bytes={have}-"} if have else {}
    limit = entry["bytes"]
    with _request(f"{base}/archive/{layer}.tar.gz", token, headers=headers) as resp:
        resumed = have if resp.status == 206 and have else None
        mode = "ab" if resumed is not None else "wb"
        total = resumed or 0
        with open(part, mode) as fh:
            while total <= limit:  # read at most one byte past the manifest's count: that byte proves the overrun
                chunk = resp.read(min(CHUNK, limit + 1 - total))
                if not chunk:
                    break
                fh.write(chunk)
                total += len(chunk)
    if total > limit:
        part.unlink()
        raise PullError(3, f"{layer}: the phone sent more than the manifest's {limit} bytes; part file deleted")
    return resumed


def _check_members(archive: Path, layer: str) -> list[tarfile.TarInfo]:
    """Every member must be a regular ``raw/<collector>/<name>.json`` of this layer; otherwise exit 4."""
    members = []
    seen_names: set[str] = set()
    max_member_bytes = 64 * 1024 * 1024
    max_total_bytes = 8 * 1024 * 1024 * 1024
    max_layer_files = 200_000
    total_bytes = 0
    with tarfile.open(archive, "r:gz") as tar:
        for info in tar:
            parts = info.name.split("/")
            total_bytes += max(0, info.size)
            ok = (
                info.isreg()
                and 0 <= info.size <= max_member_bytes
                and total_bytes <= max_total_bytes
                and len(members) < max_layer_files
                and info.name not in seen_names
                and len(parts) == 3
                and parts[0] == "raw"
                and parts[1] in LAYERS[layer]
                and parts[2] not in ("", ".", "..")
                and parts[2].endswith(".json")
                and "\\" not in info.name
            )
            if not ok:
                raise PullError(4, f"{layer}: unsafe archive member refused")
            seen_names.add(info.name)
            members.append(info)
    return members


def _set_aside(dest: Path, layer: str) -> None:
    """Rename each existing raw/<collector> of the layer into raw-stale-<date>-pull/. Never deletes."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stale = dest / f"raw-stale-{stamp}-pull"
    existing = [c for c in LAYERS[layer] if (dest / "raw" / c).exists()]
    for collector in existing:
        if (stale / collector).exists():
            raise PullError(5, f"{layer}: {stale.name}/{collector} already exists")
    for collector in existing:
        stale.mkdir(parents=True, exist_ok=True)
        os.rename(dest / "raw" / collector, stale / collector)


def _extract(archive: Path, members: list[tarfile.TarInfo], dest: Path) -> int:
    count = 0
    try:
        with tarfile.open(archive, "r:gz") as tar:
            for info in members:
                source = tar.extractfile(info)
                if source is None:
                    raise PullError(4, f"cannot extract {info.name}")
                target = dest / info.name
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp = target.with_name("." + target.name + ".tmp")
                written = 0
                try:
                    with open(tmp, "wb") as out:
                        while True:
                            buf = source.read(CHUNK)
                            if not buf:
                                break
                            written += len(buf)
                            if written > info.size:
                                raise PullError(4, f"member {info.name} exceeded header size {info.size}")
                            out.write(buf)
                    if written != info.size:
                        raise PullError(4, f"member {info.name} truncated ({written} != {info.size})")
                    os.replace(tmp, target)
                except BaseException:
                    tmp.unlink(missing_ok=True)
                    raise
                count += 1
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise PullError(4, f"corrupt archive stream: {exc}") from exc
    return count


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _push_candidates(base: str, token: str, file: Path) -> None:
    body = file.read_bytes()
    if len(body) > CANDIDATES_CAP:
        raise PullError(1, "candidates file is over the 8 MiB cap")
    with _request(f"{base}/config/candidates.jsonl", token, "PUT", {"Content-Type": "application/octet-stream"},
                  body) as resp:
        resp.read()
    _log(f"candidates: pushed {len(body)} bytes")


def _hydrate_user_cache(dest: Path) -> None:
    """The lookups of the L4 just pulled go into this root's user cache. Best effort: the layer is already pulled and
    marked, the cache is not evidence, and a failure here must not make the same pull fail (exit 5) when it is run
    again."""
    try:
        from porchlamp.collect.user_cache import hydrate_from_author_history_dir

        _log(f"user_cache: {hydrate_from_author_history_dir(dest / 'raw')} logins from L4")
    except Exception as exc:  # review-ok: gate-cache - not evidence, the pull has already succeeded
        _log(f"user_cache: not updated ({type(exc).__name__})")


def pull(base: str, token: str, dest: Path, wanted: list[str] | None) -> None:
    manifest = _get_json(f"{base}/manifest.json", token)
    layers = manifest["layers"]
    todo = [layer for layer in LAYERS if layer in layers and (wanted is None or layer in wanted)]
    pulled_dir = dest / "state" / "pulled"
    # Phase 1: download and verify every wanted layer before anything is extracted.
    plan = []
    for layer in todo:
        entry = layers[layer]
        marker = pulled_dir / f"{layer}.sha256"
        layer_dirs_present = any((dest / "raw" / c).is_dir() for c in LAYERS[layer])
        if marker.exists() and marker.read_text(encoding="utf-8").strip() == entry["sha256"] and layer_dirs_present:
            _log(f"{layer}: unchanged")
            continue
        part = dest / "pull-tmp" / f"{layer}.tar.gz.part"
        resumed = _download(base, token, layer, entry, part)
        if part.stat().st_size != entry["bytes"] or _sha256(part) != entry["sha256"]:
            part.unlink()
            raise PullError(3, f"{layer}: sha256 or size mismatch with the manifest; part file deleted")
        members = _check_members(part, layer)
        plan.append((layer, entry, part, members, resumed))
    # Phase 2: set each layer's raw folders aside, extract, and write the markers.
    for layer, entry, part, members, resumed in plan:
        _set_aside(dest, layer)
        count = _extract(part, members, dest)
        _write_text(pulled_dir / f"{layer}.sha256", entry["sha256"])
        _write_text(dest / "state" / "refresh" / f"{layer}.complete", entry["completed_at"])
        part.unlink()
        if layer == "L4":
            _hydrate_user_cache(dest)
        note = f" (resumed at {resumed})" if resumed else ""
        _log(f"{layer}: pulled files={count} bytes={entry['bytes']}{note}")
    main_done = manifest.get("main_done")
    if main_done and all((pulled_dir / f"{layer}.sha256").exists() for layer in MAIN_LAYERS):
        _write_text(dest / "state" / "refresh" / "MAIN.done", main_done)
        _log("MAIN.done written")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--dest", type=Path)
    parser.add_argument("--layers", default=None, help="comma-separated layer names; default all listed")
    parser.add_argument("--push-candidates", type=Path, default=None)
    args = parser.parse_args(argv)
    base = args.url.rstrip("/")
    try:
        token = args.token_file.read_text(encoding="utf-8").strip()
        if not token:
            raise PullError(1, "token file is empty")
        if args.push_candidates is not None:
            _push_candidates(base, token, args.push_candidates)
            return 0
        if args.dest is None:
            raise PullError(1, "--dest is required unless --push-candidates is given")
        wanted = args.layers.split(",") if args.layers else None
        if wanted and any(layer not in LAYERS for layer in wanted):
            raise PullError(1, "unknown layer in --layers")
        pull(base, token, args.dest, wanted)
        return 0
    except PullError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(f"error: {type(exc).__name__}: {exc.filename or ''}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
