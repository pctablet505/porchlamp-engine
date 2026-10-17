"""The published site tree: its IndexNow key file and the page URLs its sitemaps list.

Pure reads of a directory (the pages repository after an export); nothing here opens a socket.
``sitemap.xml`` at the root is a sitemap index whose children are files on the same host (or a
plain ``urlset``); every page URL comes back once, in sitemap order, which for the project file is
rank order.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from porchlamp.searchops.credentials import SearchOpsError

#: The IndexNow key file at the site root is ``<key>.txt``, the key 32 lowercase hex characters,
#: and the file holds the key itself (the protocol's ownership proof).
KEY_NAME_RE = re.compile(r"[0-9a-f]{32}")
SITEMAP_INDEX = "sitemap.xml"
#: The published site, as Bing knows it, and the sitemap index Search Console and Bing are sent.
SITE = "https://porchlamp.org/"
SITEMAP_URL = f"{SITE}{SITEMAP_INDEX}"
_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


class SiteError(SearchOpsError):
    """A published tree the tool cannot build a plan from."""

    exit_code = 2


@dataclass(frozen=True)
class SitemapUrls:
    """What a site's sitemaps list: one host, the sitemap files read (index first), the page URLs."""

    host: str
    files: tuple[str, ...]
    urls: tuple[str, ...]


def indexnow_key(site: Path) -> str:
    """The key of the one IndexNow key file at the root of ``site``."""
    if not site.is_dir():
        raise SiteError(f"{site} is not a directory.")
    names = sorted(path.name for path in site.glob("*.txt") if KEY_NAME_RE.fullmatch(path.stem))
    if len(names) != 1:
        raise SiteError(
            f"{site} must hold exactly one IndexNow key file (32 hex characters plus .txt) at its root; "
            f"it holds {len(names)}."
        )
    key = names[0].removesuffix(".txt")
    try:
        content = (site / names[0]).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        content = ""
    if content != key:
        raise SiteError(f"{names[0]} does not hold its own name, so IndexNow would refuse the key.")
    return key


def read_sitemaps(site: Path) -> SitemapUrls:
    """Every page URL the sitemaps of ``site`` list, each once, in sitemap order."""
    if not site.is_dir():
        raise SiteError(f"{site} is not a directory.")
    root = _parse(site / SITEMAP_INDEX)
    children: list[str] = []
    if root.tag == f"{_NS}sitemapindex":
        children = _locs(root, "sitemap")
        if not children:
            raise SiteError(f"{SITEMAP_INDEX} lists no sitemaps.")
        urls: list[str] = []
        for child in children:
            sitemap = _parse(_local_file(site, child))
            if sitemap.tag != f"{_NS}urlset":
                raise SiteError(f"{child} is not a sitemap of page URLs.")
            urls.extend(_locs(sitemap, "url"))
    elif root.tag == f"{_NS}urlset":
        urls = _locs(root, "url")
    else:
        raise SiteError(f"{SITEMAP_INDEX} is neither a sitemap index nor a sitemap.")
    urls = list(dict.fromkeys(urls))
    if not urls:
        raise SiteError(f"the sitemaps of {site} list no page URLs.")
    hosts = sorted({_https_host(url) for url in [*children, *urls]})
    if len(hosts) != 1:
        raise SiteError(f"the sitemaps name {len(hosts)} hosts, and a submission takes one: {', '.join(hosts)}.")
    return SitemapUrls(hosts[0], (f"https://{hosts[0]}/{SITEMAP_INDEX}", *children), tuple(urls))


def _parse(path: Path) -> ET.Element:
    try:
        return ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        raise SiteError(f"{path} cannot be read as XML ({error}).") from None


def _locs(root: ET.Element, entry: str) -> list[str]:
    texts = (loc.text or "" for loc in root.findall(f"{_NS}{entry}/{_NS}loc"))
    return [text.strip() for text in texts if text.strip()]


def _local_file(site: Path, url: str) -> Path:
    """The file under ``site`` that serves the sitemap ``url``."""
    path = PurePosixPath(urlsplit(url).path)
    if not path.is_absolute() or ".." in path.parts or len(path.parts) < 2:
        raise SiteError(f"{url} is not a file under the site root.")
    return site.joinpath(*path.parts[1:])


def _https_host(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc:
        raise SiteError(f"{url} is not an https URL.")
    return parts.netloc
