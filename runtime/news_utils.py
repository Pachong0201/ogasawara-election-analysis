"""Provider-independent URL normalization."""
import time
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def _canonical_url(raw: Any) -> str:
    try:
        parsed = urlparse(str(raw or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        # Preserve article identifiers in query strings; strip common trackers.
        pairs = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                 if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
        return urlunparse((parsed.scheme, parsed.hostname.lower() + (f":{parsed.port}" if parsed.port else ""),
                           parsed.path or "/", "", urlencode(sorted(pairs)), ""))
    except ValueError:
        return ""



def retry_seconds(value, now=None):
    try:
        return max(0, float(value))
    except (TypeError, ValueError):
        try:
            return max(0, parsedate_to_datetime(value).timestamp() - (time.time() if now is None else now))
        except (TypeError, ValueError, OverflowError):
            return 0

