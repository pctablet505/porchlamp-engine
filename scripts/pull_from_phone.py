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
import shutil
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
    resp, nonce = _open(url, token)
    with resp:
        raw = resp.read()
        mac = resp.headers.get(MAC_HEADER)
        status = resp.status
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
    with tarfile.open(archive, "r:gz") as tar:
        for info in tar:
            parts = info.name.split("/")
            ok = (
                info.isreg()
                and len(parts) == 3
                and parts[0] == "raw"
                and parts[1] in LAYERS[layer]
                and parts[2] not in ("", ".", "..")
                and parts[2].endswith(".json")
                and "\\" not in info.name
            )
            if not ok:
                raise PullError(4, f"{layer}: unsafe archive member refused")
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
    with tarfile.open(archive, "r:gz") as tar:
        for info in members:
            source = tar.extractfile(info)
            target = dest / info.name
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name("." + target.name + ".tmp")
            with open(tmp, "wb") as out:
                shutil.copyfileobj(source, out, CHUNK)
            os.replace(tmp, target)
            count += 1
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


def _push_user_cache(base: str, token: str, dest: Path) -> None:
    from porchlamp.collect.user_cache import UserCache, export_sqlite_gz, hydrate_from_author_history_dir

    cache = UserCache(dest / "state" / "users", backend="sqlite")
    try:
        hydrate_from_author_history_dir(dest / "raw", cache=cache)
        body = export_sqlite_gz(cache)
    finally:
        cache.close()
    if len(body) > CANDIDATES_CAP:
        raise PullError(1, "user cache archive is over the 8 MiB cap")
    with _request(f"{base}/state/users.sqlite.gz", token, "PUT", {"Content-Type": "application/gzip"}, body) as resp:
        resp.read()
    _log(f"user_cache: pushed {len(body)} bytes")


def _pull_user_cache(base: str, token: str, dest: Path) -> None:
    from porchlamp.collect.user_cache import UserCache, import_sqlite_gz

    resp, nonce = _open(f"{base}/state/users.sqlite.gz", token)
    with resp:
        raw = resp.read()
        mac = resp.headers.get(MAC_HEADER)
        status = resp.status
    if not verify_response(token, nonce, status, raw, mac):
        raise PullError(1, "GET state/users.sqlite.gz: reply signature missing or wrong; nothing used")
    cache = UserCache(dest / "state" / "users", backend="sqlite")
    try:
        imported = import_sqlite_gz(cache, raw)
    except ValueError as exc:
        raise PullError(3, f"user_cache: invalid sqlite.gz payload ({exc})") from None
    finally:
        cache.close()
    _log(f"user_cache: pulled {imported} records ({len(raw)} bytes)")


def pull(base: str, token: str, dest: Path, wanted: list[str] | None) -> None:
    from porchlamp.collect.user_cache import hydrate_from_author_history_dir

    manifest = _get_json(f"{base}/manifest.json", token)
    layers = manifest["layers"]
    todo = [layer for layer in LAYERS if layer in layers and (wanted is None or layer in wanted)]
    pulled_dir = dest / "state" / "pulled"
    # Phase 1: download and verify every wanted layer before anything is extracted.
    plan = []
    for layer in todo:
        entry = layers[layer]
        marker = pulled_dir / f"{layer}.sha256"
        if marker.exists() and marker.read_text(encoding="utf-8").strip() == entry["sha256"]:
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
        if layer == "L4":
            hydrate_from_author_history_dir(dest / "raw")
        _write_text(pulled_dir / f"{layer}.sha256", entry["sha256"])
        _write_text(dest / "state" / "refresh" / f"{layer}.complete", entry["completed_at"])
        part.unlink()
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
    parser.add_argument("--push-user-cache", action="store_true", help="push state/users.sqlite.gz to phone")
    parser.add_argument("--pull-user-cache", action="store_true", help="pull state/users.sqlite.gz from phone")
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
        if args.push_user_cache:
            _push_user_cache(base, token, args.dest)
            return 0
        if args.pull_user_cache:
            _pull_user_cache(base, token, args.dest)
            return 0
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
