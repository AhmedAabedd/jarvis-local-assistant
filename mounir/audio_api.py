"""Small helpers shared by OpenAI-compatible speech transports."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def endpoint_url(value: str, operation: str) -> str:
    """Return an API operation URL from either an API root or full endpoint.

    Keeping a query string matters for compatible services such as Azure, whose
    API version is commonly supplied in the endpoint URL.
    """
    raw = str(value or "").strip()
    if not raw:
        raise RuntimeError(
            "This voice connection needs an API base URL or full endpoint URL."
        )
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("The voice API URL must start with http:// or https://.")
    suffix = "/" + operation.strip("/")
    path = parsed.path.rstrip("/")
    if not path.endswith(suffix):
        path += suffix
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


def bearer_headers(api_key: str | None, *, accept: str) -> dict[str, str]:
    headers = {"Accept": accept}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers
