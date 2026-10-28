"""Minimal JSON-over-HTTP helpers shared by the provider and email clients."""
import json
import urllib.error
import urllib.request


def http_error_with_body(exc):
    """Rebuild an HTTPError with the response body folded into its message.

    The API's response body carries the real explanation (e.g. which parameter
    was invalid). urllib otherwise discards it, leaving only a useless bare
    "HTTP Error 400: Bad Request" with no way to tell what went wrong.
    """
    detail = exc.read().decode("utf-8", errors="replace")
    return urllib.error.HTTPError(
        exc.url, exc.code, f"{exc.reason}: {detail}", exc.headers, None
    )


def post_json(url, headers, payload, timeout):
    """POST a JSON payload and return the decoded JSON response."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise http_error_with_body(exc) from None
