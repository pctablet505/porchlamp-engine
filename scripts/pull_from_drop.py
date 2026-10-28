#!/usr/bin/env python3
"""Pull a finished phone cycle from a dedicated private GitHub drop-box repository into a data root.

    python scripts/pull_from_drop.py --repo owner/porchlamp-drop --dest DATA_ROOT [--token-file F]
    python scripts/pull_from_drop.py --repo owner/porchlamp-drop --dest DATA_ROOT --check-only

Exit codes: 0 ok, 1 usage or HTTP/network error, 3 sha256/size mismatch (nothing extracted),
4 unsafe archive member (nothing extracted), 5 a set-aside target already exists (nothing moved).
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
from typing import Any
from urllib import error, parse, request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phone.archive import LAYERS, MAIN_LAYERS
from phone.control import valid_drop_repo

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "porchlamp-drop-pull/1"
CHUNK = 1 << 20
TIMEOUT_S = 60
ASSET_TIMEOUT_S = 300
CANDIDATES_CAP = 16 * 1024 * 1024
MANIFEST_CAP = 4 * 1024 * 1024


class PullError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class _NoRedirect(request.HTTPRedirectHandler):
    """Never automatically forward an Authorization header across redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = request.build_opener(_NoRedirect)


def _log(message: str) -> None:
    print(message, flush=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_text(path, json.dumps(payload, indent=2, sort_keys=True))


def _is_loopback_base(api_base: str) -> bool:
    host = (parse.urlsplit(api_base).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def _api_get_json(url: str, token: str) -> Any:
    req = request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    try:
        with _NO_REDIRECT_OPENER.open(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read()
    except error.HTTPError as exc:
        raise PullError(1, f"GET {parse.urlsplit(url).path} failed: HTTP {exc.code}") from None
    except (error.URLError, OSError) as exc:
        raise PullError(1, f"GET {parse.urlsplit(url).path} failed: {type(exc).__name__}") from None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        raise PullError(1, f"GET {parse.urlsplit(url).path}: invalid JSON reply") from None


def _open_asset_stream(api_base: str, repo: str, asset_id: int, token: str):
    """Open a binary release asset stream, stripping Authorization if GitHub redirects to blob storage."""
    url = f"{api_base}/repos/{repo}/releases/assets/{int(asset_id)}"
    req = request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/octet-stream",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    try:
        return _NO_REDIRECT_OPENER.open(req, timeout=ASSET_TIMEOUT_S)
    except error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            location = exc.headers.get("Location") if exc.headers is not None else None
            exc.close()
            if not location:
                raise PullError(1, f"asset {asset_id}: redirect missing Location header") from None
            parts = parse.urlsplit(location)
            allow_http = _is_loopback_base(api_base) and (parts.hostname or "").lower() in ("127.0.0.1", "localhost")
            if parts.scheme != "https" and not (allow_http and parts.scheme == "http"):
                raise PullError(1, f"asset {asset_id}: refused non-HTTPS redirect target") from None
            follow = request.Request(location, headers={"User-Agent": USER_AGENT}, method="GET")
            try:
                return _NO_REDIRECT_OPENER.open(follow, timeout=ASSET_TIMEOUT_S)
            except error.HTTPError as redirect_exc:
                raise PullError(1, f"asset {asset_id} redirect failed: HTTP {redirect_exc.code}") from None
            except (error.URLError, OSError) as redirect_exc:
                raise PullError(1, f"asset {asset_id} redirect failed: {type(redirect_exc).__name__}") from None
        exc.close()
        raise PullError(1, f"asset {asset_id} download failed: HTTP {exc.code}") from None
    except (error.URLError, OSError) as exc:
        raise PullError(1, f"asset {asset_id} download failed: {type(exc).__name__}") from None


def _download_bounded(api_base: str, repo: str, asset_id: int, token: str, out_path: Path, limit: int) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with _open_asset_stream(api_base, repo, asset_id, token) as resp, open(out_path, "wb") as fh:
        while total <= limit:
            chunk = resp.read(min(CHUNK, limit + 1 - total))
            if not chunk:
                break
            fh.write(chunk)
            total += len(chunk)
    if total > limit:
        out_path.unlink(missing_ok=True)
        raise PullError(3, f"asset {asset_id} exceeded expected size ({limit} bytes); deleted")


def _select_release(api_base: str, repo: str, token: str, tag: str | None) -> dict[str, Any] | None:
    if tag:
        encoded = parse.quote(tag, safe="")
        rel = _api_get_json(f"{api_base}/repos/{repo}/releases/tags/{encoded}", token)
        if not isinstance(rel, dict) or rel.get("draft") is True:
            raise PullError(1, f"release {tag} is missing or still a draft")
        return rel
    releases = _api_get_json(f"{api_base}/repos/{repo}/releases?per_page=20", token)
    if not isinstance(releases, list):
        raise PullError(1, "unexpected releases list reply")
    for rel in releases:
        if (
            isinstance(rel, dict)
            and rel.get("draft") is False
            and rel.get("prerelease") is False
            and isinstance(rel.get("tag_name"), str)
            and rel["tag_name"].startswith("cycle-")
        ):
            return rel
    return None


def _assets_by_name(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for asset in release.get("assets") or []:
        if (
            isinstance(asset, dict)
            and isinstance(asset.get("name"), str)
            and asset.get("state") in (None, "uploaded")
            and "id" in asset
        ):
            out[asset["name"]] = asset
    return out


def _read_pulled_release(dest: Path) -> dict[str, Any] | None:
    path = dest / "state" / "pulled" / "release.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _check_members(archive_path: Path, layer: str) -> list[tarfile.TarInfo]:
    """Every member must be a regular ``raw/<collector>/<name>.json`` of this layer; otherwise exit 4."""
    members: list[tarfile.TarInfo] = []
    with tarfile.open(archive_path, "r:gz") as tar:
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


def _extract(archive_path: Path, members: list[tarfile.TarInfo], dest: Path) -> int:
    count = 0
    with tarfile.open(archive_path, "r:gz") as tar:
        for info in members:
            source = tar.extractfile(info)
            if source is None:
                raise PullError(4, f"missing payload for {info.name}")
            target = dest / info.name
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name("." + target.name + ".tmp")
            with open(tmp, "wb") as out:
                shutil.copyfileobj(source, out, CHUNK)
            os.replace(tmp, target)
            count += 1
    return count


def _download_layer_archive(
    api_base: str,
    repo: str,
    token: str,
    layer: str,
    entry: dict[str, Any],
    assets: dict[str, dict[str, Any]],
    tmp_dir: Path,
) -> Path:
    target = tmp_dir / f"{layer}.tar.gz"
    parts = entry.get("parts")
    if isinstance(parts, list) and parts:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "wb") as combined:
            for idx, part_meta in enumerate(parts):
                if not isinstance(part_meta, dict):
                    raise PullError(1, f"{layer}: malformed part metadata at index {idx}")
                part_name = str(part_meta.get("name") or "")
                part_bytes = int(part_meta.get("bytes") or -1)
                part_sha = str(part_meta.get("sha256") or "")
                asset = assets.get(part_name)
                if asset is None or part_bytes < 0 or not part_sha:
                    raise PullError(1, f"{layer}: release asset {part_name!r} is missing")
                slice_tmp = tmp_dir / f"{part_name}.tmp"
                _download_bounded(api_base, repo, int(asset["id"]), token, slice_tmp, part_bytes)
                if slice_tmp.stat().st_size != part_bytes or _sha256(slice_tmp) != part_sha:
                    slice_tmp.unlink(missing_ok=True)
                    target.unlink(missing_ok=True)
                    raise PullError(3, f"{layer}: sha256 or size mismatch in {part_name}")
                with open(slice_tmp, "rb") as sfh:
                    shutil.copyfileobj(sfh, combined, CHUNK)
                slice_tmp.unlink(missing_ok=True)
    else:
        arc_name = str(entry.get("archive") or f"{layer}.tar.gz")
        asset = assets.get(arc_name)
        if asset is None:
            raise PullError(1, f"{layer}: release asset {arc_name!r} is missing")
        _download_bounded(api_base, repo, int(asset["id"]), token, target, int(entry["bytes"]))

    if target.stat().st_size != int(entry["bytes"]) or _sha256(target) != entry["sha256"]:
        target.unlink(missing_ok=True)
        raise PullError(3, f"{layer}: sha256 or size mismatch with manifest.json; archive deleted")
    return target


def pull_from_drop(
    *,
    api_base: str,
    repo: str,
    token: str,
    dest: Path,
    tag: str | None = None,
    wanted: list[str] | None = None,
    check_only: bool = False,
) -> dict[str, Any]:
    release = _select_release(api_base, repo, token, tag)
    if release is None:
        return {"has_new": False, "tag": None, "reason": "no_cycle_release"}

    rel_tag = str(release["tag_name"])
    prev = _read_pulled_release(dest)
    if prev and prev.get("tag") == rel_tag and prev.get("repo") == repo and wanted is None:
        return {
            "has_new": False,
            "tag": rel_tag,
            "as_of": prev.get("main_done"),
            "reason": "already_pulled",
        }
    if check_only:
        return {"has_new": True, "tag": rel_tag, "reason": "new_release"}

    assets = _assets_by_name(release)
    manifest_asset = assets.get("manifest.json")
    if manifest_asset is None:
        raise PullError(1, f"release {rel_tag} is missing manifest.json")

    tmp_dir = dest / "pull-tmp"
    manifest_path = tmp_dir / "manifest.json"
    _download_bounded(api_base, repo, int(manifest_asset["id"]), token, manifest_path, MANIFEST_CAP)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except ValueError:
        manifest_path.unlink(missing_ok=True)
        raise PullError(1, f"release {rel_tag}: manifest.json is not valid JSON") from None
    manifest_path.unlink(missing_ok=True)

    if not isinstance(manifest, dict) or not isinstance(manifest.get("layers"), dict):
        raise PullError(1, f"release {rel_tag}: manifest.json missing layers map")

    # Download and verify candidates.jsonl when listed in the manifest.
    cand_sha = manifest.get("candidates_sha256")
    if cand_sha:
        cand_asset = assets.get("candidates.jsonl")
        if cand_asset is None:
            raise PullError(1, f"release {rel_tag} is missing candidates.jsonl")
        cand_limit = int(manifest.get("candidates_bytes") or CANDIDATES_CAP)
        if cand_limit > CANDIDATES_CAP:
            raise PullError(3, f"candidates.jsonl exceeds {CANDIDATES_CAP} byte cap")
        cand_tmp = tmp_dir / "candidates.jsonl.tmp"
        _download_bounded(api_base, repo, int(cand_asset["id"]), token, cand_tmp, cand_limit)
        if _sha256(cand_tmp) != cand_sha:
            cand_tmp.unlink(missing_ok=True)
            raise PullError(3, "candidates.jsonl sha256 mismatch with manifest.json")
        cand_dest = dest / "raw" / "candidates.jsonl"
        cand_dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(cand_tmp, cand_dest)
        _log(f"candidates.jsonl: verified sha256={cand_sha[:12]}")

    layers = manifest["layers"]
    todo = [layer for layer in LAYERS if layer in layers and (wanted is None or layer in wanted)]
    pulled_dir = dest / "state" / "pulled"
    pulled_layers: list[str] = []

    for layer in todo:
        entry = layers[layer]
        marker = pulled_dir / f"{layer}.sha256"
        if marker.exists() and marker.read_text(encoding="utf-8").strip() == entry["sha256"]:
            _log(f"{layer}: unchanged")
            continue
        arc_path = _download_layer_archive(api_base, repo, token, layer, entry, assets, tmp_dir)
        try:
            members = _check_members(arc_path, layer)
            _set_aside(dest, layer)
            count = _extract(arc_path, members, dest)
            _write_text(pulled_dir / f"{layer}.sha256", str(entry["sha256"]))
            _write_text(dest / "state" / "refresh" / f"{layer}.complete", str(entry["completed_at"]))
            pulled_layers.append(layer)
            _log(f"{layer}: pulled files={count} bytes={entry['bytes']}")
        finally:
            arc_path.unlink(missing_ok=True)

    if tmp_dir.is_dir():
        shutil.rmtree(tmp_dir, ignore_errors=True)

    main_done = manifest.get("main_done")
    if main_done and all((pulled_dir / f"{layer}.sha256").exists() for layer in MAIN_LAYERS):
        _write_text(dest / "state" / "refresh" / "MAIN.done", str(main_done))
        _log("MAIN.done written")

    as_of = str(main_done or manifest.get("generated_at") or "")
    release_state = {
        "repo": repo,
        "tag": rel_tag,
        "main_done": main_done,
        "as_of": as_of,
        "pulled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "layers": list(layers.keys()),
    }
    _write_json(pulled_dir / "release.json", release_state)
    return {
        "has_new": True,
        "tag": rel_tag,
        "as_of": as_of,
        "pulled_layers": pulled_layers,
        "reason": "pulled",
    }


def _read_token(token_file: Path | None) -> str:
    if token_file is not None:
        token = token_file.read_text(encoding="utf-8").strip()
    else:
        token = (os.environ.get("PORCHLAMP_DROP_READ_TOKEN") or "").strip()
    if not token:
        raise PullError(1, "missing drop-box read token (pass --token-file or set PORCHLAMP_DROP_READ_TOKEN)")
    return token


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", required=True, help="private drop-box repository owner/name")
    parser.add_argument("--dest", required=True, type=Path, help="destination data directory")
    parser.add_argument("--token-file", type=Path, default=None, help="path to fine-grained read token")
    parser.add_argument("--tag", default=None, help="specific cycle-* release tag (default: newest)")
    parser.add_argument("--layers", default=None, help="comma-separated layer names; default all listed")
    parser.add_argument("--check-only", action="store_true", help="check whether a new cycle release exists")
    parser.add_argument("--status-out", type=Path, default=None, help="write JSON result summary to this path")
    parser.add_argument("--api-base", default=API_BASE, help="GitHub REST API base URL")
    args = parser.parse_args(argv)

    try:
        repo = valid_drop_repo(args.repo)
        if repo is None:
            raise PullError(1, "invalid --repo (must be a dedicated owner/name, never porchlamp or engine repos)")
        token = _read_token(args.token_file)
        wanted = args.layers.split(",") if args.layers else None
        if wanted and any(layer not in LAYERS for layer in wanted):
            raise PullError(1, "unknown layer in --layers")
        summary = pull_from_drop(
            api_base=args.api_base.rstrip("/"),
            repo=repo,
            token=token,
            dest=args.dest,
            tag=args.tag,
            wanted=wanted,
            check_only=args.check_only,
        )
        if args.status_out is not None:
            _write_json(args.status_out, summary)
        if args.check_only:
            _log(json.dumps(summary, sort_keys=True))
        return 0
    except PullError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(f"error: {type(exc).__name__}: {exc.filename or ''}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
