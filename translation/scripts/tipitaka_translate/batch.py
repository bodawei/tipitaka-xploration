"""Asynchronous batch submission and collection for providers that offer it.

Claude (Anthropic Message Batches, 50% off) and Kimi (Moonshot batch API, 40%
off) process a set of requests in the background, usually within the hour and
at most within 24 hours. The job submits a chunk's requests on one run and
collects the results on the next (see job.py). DeepSeek has no batch API and is
always called directly.

Every request in a batch carries a `custom_id` (e.g. "translate-eng"); results
come back keyed by it, in no particular order.
"""
import json

from .http import get_json, get_text, post_json, post_multipart
from .providers import (
    ANTHROPIC_BASE,
    KIMI_BASE,
    anthropic_headers,
    anthropic_params,
    bearer_headers,
    chat_completion_body,
    parse_anthropic,
    parse_chat_completion,
)

BATCH_CAPABLE = ("claude", "kimi")

DISCOUNT = {"claude": "50% off", "kimi": "40% off"}

# Kimi batch statuses after which nothing more will happen.
KIMI_TERMINAL = {"completed", "failed", "expired", "cancelled"}


def submit(provider, requests):
    """Submit {custom_id: {"system", "user", "hint"}} as one batch; return its id."""
    name, key, model = provider["name"], provider["key"], provider["model"]
    if name == "claude":
        result = post_json(
            f"{ANTHROPIC_BASE}/messages/batches",
            anthropic_headers(key),
            {
                "requests": [
                    {"custom_id": cid, "params": anthropic_params(model, r["system"], r["user"])}
                    for cid, r in requests.items()
                ]
            },
            120,
        )
        return result["id"]
    if name == "kimi":
        lines = [
            json.dumps(
                {
                    "custom_id": cid,
                    "method": "POST",
                    "url": "/v1/chat/completions",
                    "body": chat_completion_body("kimi", model, r["system"], r["user"]),
                },
                ensure_ascii=False,
            )
            for cid, r in requests.items()
        ]
        auth = {"authorization": f"Bearer {key}"}
        uploaded = post_multipart(
            f"{KIMI_BASE}/files", auth, {"purpose": "batch"},
            "file", "requests.jsonl", ("\n".join(lines) + "\n").encode("utf-8"), 120,
        )
        result = post_json(
            f"{KIMI_BASE}/batches",
            bearer_headers(key),
            {
                "input_file_id": uploaded["id"],
                "endpoint": "/v1/chat/completions",
                "completion_window": "24h",
            },
            120,
        )
        return result["id"]
    raise ValueError(f"Provider {name!r} has no batch API")


def _retrieve(provider, batch_id):
    if provider["name"] == "claude":
        return get_json(
            f"{ANTHROPIC_BASE}/messages/batches/{batch_id}",
            anthropic_headers(provider["key"]), 60,
        )
    return get_json(
        f"{KIMI_BASE}/batches/{batch_id}", bearer_headers(provider["key"]), 60
    )


def status(provider, batch_id):
    """Return (finished, status_text) for a submitted batch."""
    info = _retrieve(provider, batch_id)
    if provider["name"] == "claude":
        return info["processing_status"] == "ended", info["processing_status"]
    return info["status"] in KIMI_TERMINAL, info["status"]


def results(provider, batch_id, hints):
    """Fetch a finished batch's results.

    `hints` maps each expected custom_id to its truncation message. Returns
    {custom_id: (text, usage)} for the requests that succeeded, and a list of
    error strings for the ones that did not (including any expected id that
    is missing from the results altogether).
    """
    name, key = provider["name"], provider["key"]
    info = _retrieve(provider, batch_id)
    succeeded, errors = {}, []

    def record_success(cid, parse, payload):
        try:
            succeeded[cid] = parse(payload, hints.get(cid, "Output truncated"))
        except Exception as exc:
            errors.append(f"{cid}: {exc}")

    if name == "claude":
        for line in get_text(info["results_url"], anthropic_headers(key), 120).splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            cid, outcome = item["custom_id"], item["result"]
            if outcome["type"] == "succeeded":
                record_success(cid, parse_anthropic, outcome["message"])
            else:
                errors.append(f"{cid}: {outcome['type']} {outcome.get('error', '')}".strip())
    else:
        auth = bearer_headers(key)
        if info.get("output_file_id"):
            content = get_text(f"{KIMI_BASE}/files/{info['output_file_id']}/content", auth, 120)
            for line in content.splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                cid = item.get("custom_id")
                response = item.get("response") or {}
                if item.get("error") or response.get("status_code") != 200:
                    errors.append(f"{cid}: {item.get('error') or response}")
                else:
                    record_success(cid, parse_chat_completion, response["body"])
        if info.get("error_file_id"):
            content = get_text(f"{KIMI_BASE}/files/{info['error_file_id']}/content", auth, 120)
            errors.extend(f"error file: {line[:500]}" for line in content.splitlines() if line.strip())
        if info["status"] != "completed":
            errors.append(f"batch ended with status {info['status']!r}")

    for cid in hints:
        if cid not in succeeded and not any(e.startswith(f"{cid}:") for e in errors):
            errors.append(f"{cid}: missing from batch results")
    return succeeded, errors


def cancel(provider, batch_id):
    """Best-effort cancellation, used when a multi-provider submission fails
    partway so an orphaned batch does not run (and bill) for nothing."""
    try:
        if provider["name"] == "claude":
            post_json(
                f"{ANTHROPIC_BASE}/messages/batches/{batch_id}/cancel",
                anthropic_headers(provider["key"]), {}, 60,
            )
        else:
            post_json(
                f"{KIMI_BASE}/batches/{batch_id}/cancel",
                bearer_headers(provider["key"]), {}, 60,
            )
    except Exception as exc:
        print(f"Could not cancel {provider['name']} batch {batch_id}: {exc}")
