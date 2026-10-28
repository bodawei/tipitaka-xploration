"""Email delivery via Resend, with a durable spool for failed sends.

A send failure must not abort the run: the translation has already been made
and archived, so rolling back would only re-translate and re-send the same
chunk. Instead the exact message is written to the spool and replayed at the
start of a later run.
"""
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from .http import http_error_with_body

RESEND_URL = "https://api.resend.com/emails"


def warn(message):
    """Log a non-fatal problem so it is visible without failing the run."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::warning::{message}")
    else:
        print(f"WARNING: {message}")


@dataclass(frozen=True)
class EmailAccount:
    """Resend credentials, resolved from the environment."""

    api_key: str
    sender: str
    recipient: str

    @classmethod
    def from_environment(cls):
        return cls(
            api_key=os.environ["RESEND_API_KEY"],
            sender=os.environ["EMAIL_FROM"],
            recipient=os.environ["EMAIL_TO"],
        )


def send_email(account, subject, text_body):
    body = json.dumps(
        {
            "from": account.sender,
            "to": [account.recipient],
            "subject": subject,
            "text": text_body,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        RESEND_URL,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {account.api_key}",
            "user-agent": "tipitaka-xploration-daily-translate/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise http_error_with_body(exc) from None


def spool_email(spool_dir, subject, body, context, error):
    """Persist an email that could not be sent, for a later run to retry.

    The recipient is deliberately not stored: it is a secret, and resolving it
    from the environment at send time means a retry honours its current value.
    """
    os.makedirs(spool_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for seq in range(1, 1000):
        path = os.path.join(spool_dir, f"email-{stamp}-{seq:03d}.json")
        if not os.path.exists(path):
            break
    else:
        raise RuntimeError(f"Email spool is full for timestamp {stamp}")

    write_spool_record(
        path,
        {
            "queued": stamp,
            "context": context,
            "subject": subject,
            "body": body,
            "attempts": 1,
            "last_error": error,
        },
    )
    return path


def write_spool_record(path, record):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
        f.write("\n")


def send_email_or_spool(account, spool_dir, subject, body, context):
    """Send an email; on failure spool it to disk rather than failing the run.

    A send failure must not abort the run. spool_email keeps the exact message
    for flush_pending_emails() to retry at the start of a later run.
    """
    try:
        send_email(account, subject, body)
        return True
    except Exception as exc:
        path = spool_email(
            spool_dir, subject, body, context, f"{type(exc).__name__}: {exc}"
        )
        warn(
            f"Email failed ({context}): {exc} — spooled to {path} "
            f"for retry on the next run"
        )
        return False


def flush_pending_emails(account, spool_dir):
    """Retry emails spooled by earlier runs, oldest first.

    Stops at the first failure so the queue keeps its order and a mail service
    that is still down is not hammered; whatever is left waits for the run
    after this one.
    """
    if not os.path.isdir(spool_dir):
        return
    names = sorted(n for n in os.listdir(spool_dir) if n.endswith(".json"))
    if not names:
        return

    print(f"Retrying {len(names)} email(s) spooled by earlier runs")
    for position, name in enumerate(names):
        path = os.path.join(spool_dir, name)
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
        try:
            send_email(account, record["subject"], record["body"])
        except Exception as exc:
            record["attempts"] = record.get("attempts", 1) + 1
            record["last_error"] = f"{type(exc).__name__}: {exc}"
            write_spool_record(path, record)
            warn(
                f"Spooled email still failing ({record.get('context')}): {exc} — "
                f"{len(names) - position} message(s) left queued"
            )
            return
        os.remove(path)
        print(f"  resent: {record['subject']}")
