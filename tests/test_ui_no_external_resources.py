"""Zero-external-request check for `web/v2/` (06 §8 checklist item 10).

`tests/test_webapp.py::TestIndexServed::test_page_is_fully_offline` is the
existing v1 check this packet was told to find and extend — it inspects
`web/index.html` alone, which is a single-file v1 app. `web/v2/` is many
files (styles/*.css, *.js, components/*.js, views/*.js), so this test
extends the SAME rule to every file actually served, rather than editing the
v1 test suite verification.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB_V2 = Path(__file__).resolve().parent.parent / "web" / "v2"

#: Every file this packet ships that a browser will actually fetch.
SERVED_EXTENSIONS = {".html", ".css", ".js", ".mjs"}

#: The char right after "//" must be alphanumeric (a host name never starts
#: with punctuation) — this is what tells a real "//example.com" URL apart
#: from a `//:`-prefixed doc comment (this file's own house style; see
#: web/v2/state.js), which would otherwise false-positive.
EXTERNAL_URL_RE = re.compile(r"""(https?:)?//[a-zA-Z0-9][^\s'"()]*""")
#: The only "//" this codebase may legitimately contain: outbound
#: `https://github.com/<owner>/<name>` anchor links rendered in the repository
#: drawer (`web/v2/copy.js::drawerGithubUrl`), which the browser never fetches
#: automatically as a subresource. Kept as an explicit allowlist so any other
#: external URL is still flagged.
ALLOWED_URL_SUBSTRINGS: tuple[str, ...] = ("https://github.com/",)


#: Link-preview, canonical, alternate (hreflang/feed), and JSON-LD structured
#: data tags must carry absolute URLs -- crawlers do not resolve relative ones
#: -- yet the browser never fetches them as subresources. Only the content/href
#: of such an inert SEO tag is exempt; the same URL anywhere else in a served
#: file is still a violation.
LINK_PREVIEW_META_RE = re.compile(
    r"""<meta\s+(?:property="og:[\w:]+"|name="twitter:[\w:]+")\s+content="[^"]*">|<link\s+rel="(?:canonical|alternate)"[^>]*href="[^"]*">|<script\s+type="application/ld\+json">[\s\S]*?</script>"""
)


def served_files() -> list[Path]:
    return [p for p in WEB_V2.rglob("*") if p.is_file() and p.suffix in SERVED_EXTENSIONS]


def scannable_text(text: str) -> str:
    """The text a browser could turn into a request: link-preview <meta> and canonical <link> tags removed."""
    return LINK_PREVIEW_META_RE.sub("", text)


class TestNoExternalResources:
    def test_every_served_file_exists(self) -> None:
        files = served_files()
        assert len(files) >= 15, f"expected at least 15 served files under web/v2/, found {len(files)}"

    def test_no_external_script_or_style_tags(self) -> None:
        for path in served_files():
            if path.suffix != ".html":
                continue
            html = scannable_text(path.read_text())
            assert "<script src=\"http" not in html, f"{path}: external <script src>"
            assert "<link" not in html or 'href="http' not in html, f"{path}: external <link>"

    def test_no_external_urls_anywhere(self) -> None:
        violations: list[str] = []
        for path in served_files():
            text = scannable_text(path.read_text())
            for match in EXTERNAL_URL_RE.finditer(text):
                url = match.group(0)
                if any(allowed in url for allowed in ALLOWED_URL_SUBSTRINGS):
                    continue
                violations.append(f"{path.relative_to(WEB_V2)}: {url}")
        assert not violations, "external URL(s) found:\n" + "\n".join(violations)

    def test_no_google_fonts_or_webfont_at_import(self) -> None:
        for path in served_files():
            text = path.read_text()
            assert "fonts.googleapis" not in text, f"{path}: Google Fonts reference"
            assert "fonts.gstatic" not in text, f"{path}: Google Fonts reference"
            assert "@font-face" not in text, f"{path}: a @font-face rule always names an external or embedded font file — this project uses the system font stack only"

    def test_no_cdn_hosts(self) -> None:
        cdn_hosts = ("cdnjs.cloudflare.com", "cdn.jsdelivr.net", "unpkg.com", "esm.sh", "jquery.com")
        for path in served_files():
            text = path.read_text()
            for host in cdn_hosts:
                assert host not in text, f"{path}: references CDN host {host}"

    def test_files_are_valid_utf8_with_no_nul_bytes(self) -> None:
        # A regression guard: this exact defect (stray NUL bytes silently
        # corrupting a template-literal line) was caught and fixed while
        # building this package.
        for path in served_files():
            data = path.read_bytes()
            assert b"\x00" not in data, f"{path}: contains a NUL byte"
            data.decode("utf-8")  # raises UnicodeDecodeError on invalid UTF-8


def test_link_preview_exemption_covers_only_preview_meta_tags() -> None:
    url = "https://example.org/og.png"
    exempt = (
        f'<meta property="og:image" content="{url}">\n'
        f'<meta name="twitter:image" content="{url}">\n'
        f'<link rel="canonical" href="{url}">'
    )
    assert not EXTERNAL_URL_RE.search(scannable_text(exempt))
    for still_flagged in (
        f'<img src="{url}">',
        f'<meta http-equiv="refresh" content="0; url={url}">',
        f'<link rel="preload" href="{url}">',
        f'<link rel="stylesheet" href="{url}">',
        f'const u = "{url}";',
    ):
        assert EXTERNAL_URL_RE.search(scannable_text(still_flagged)), still_flagged
