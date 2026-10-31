"""Minimal JSON-over-HTTP helpers shared by the provider, batch, and email clients."""
import json
import urllib.error
import urllib.request
import uuid


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


def _send(req, timeout):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise http_error_with_body(exc) from None


def post_json(url, headers, payload, timeout):
    """POST a JSON payload and return the decoded JSON response."""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    return json.loads(_send(req, timeout).decode("utf-8"))


def get_text(url, headers, timeout):
    """GET a URL and return the response body as text."""
    req = urllib.request.Request(url, method="GET", headers=headers)
    return _send(req, timeout).decode("utf-8")


def get_json(url, headers, timeout):
    """GET a URL and return the decoded JSON response."""
    return json.loads(get_text(url, headers, timeout))


def post_multipart(url, headers, fields, file_field, filename, file_bytes, timeout):
    """POST a multipart/form-data upload (plain fields plus one file) and
    return the decoded JSON response."""
    boundary = f"----tipitaka{uuid.uuid4().hex}"
    parts = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n".encode("utf-8")
        + file_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    req = urllib.request.Request(
        url,
        data=b"".join(parts),
        method="POST",
        headers={**headers, "content-type": f"multipart/form-data; boundary={boundary}"},
    )
    return json.loads(_send(req, timeout).decode("utf-8"))
