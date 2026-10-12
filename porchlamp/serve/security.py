"""The request-size cap and the response headers every Porchlamp HTTP server applies.

Standard library only, so the pydantic-only fixture server and the FastAPI app share one definition.
"""

from __future__ import annotations

#: Largest request body a server reads (the reproject weights are a few hundred bytes).
MAX_REQUEST_BODY_BYTES: int = 65_536

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: https:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    ),
}
