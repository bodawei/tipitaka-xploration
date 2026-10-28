"""Failure diagnosis and the error-report email.

When a run fails, DeepSeek is asked for a root-cause analysis (independent of
whether DeepSeek is an enabled translation provider), and the diagnosis is
appended to the error-report email.
"""
import os
import sys
import traceback

from .config import PROVIDERS
from .mail import send_email_or_spool
from .providers import deepseek_message

DIAGNOSIS_SYSTEM = (
    "You are a senior software engineer debugging a Python batch job: a daily "
    "script that reads Pali (IAST) verses from the Tipitaka and translates them "
    "into English and Traditional Chinese by calling LLM provider HTTP APIs "
    "(Anthropic/Claude, Moonshot/Kimi, DeepSeek) and emailing the results via "
    "Resend. You are given exactly what the job was doing when it failed, the "
    "exception, and the full traceback. State the single most likely root cause "
    "and the concrete fix (name the file, function, parameter, or env var to "
    "change). HTTP 4xx bodies from the provider APIs usually name the offending "
    "parameter — read them closely. If the evidence is genuinely ambiguous, give "
    "the top one or two causes and say what extra information would settle it. "
    "Be specific and practical; no preamble, no padding."
)


def diagnose_failure(context, exc, tb):
    """Ask DeepSeek to analyse a job failure and return its written diagnosis.

    DeepSeek is used here as a debugging aide independent of whether it is an
    enabled translation provider: it just needs DEEPSEEK_API_KEY. Any problem
    producing the diagnosis (no key, network error, truncation) is reported
    inline rather than raised — diagnosis must never derail the error report
    that carries it.
    """
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return "DeepSeek diagnosis skipped: DEEPSEEK_API_KEY is not set."
    model = os.environ.get(
        PROVIDERS["deepseek"]["model_env"], PROVIDERS["deepseek"]["model_default"]
    )
    user_msg = (
        "The daily translation job (translation/scripts/tipitaka_translate/, "
        "entry point translation/scripts/daily_translate.py) failed. Diagnose it.\n\n"
        f"What it was doing (context): {context}\n\n"
        f"Exception: {type(exc).__name__}: {exc}\n\n"
        f"Full traceback:\n{tb}"
    )
    try:
        return deepseek_message(
            api_key, model, DIAGNOSIS_SYSTEM, user_msg,
            "Diagnosis truncated: hit max_tokens output cap.",
        )
    except Exception as diag_exc:
        return f"DeepSeek diagnosis unavailable ({type(diag_exc).__name__}: {diag_exc})."


def send_error_report(account, spool_dir, context, exc):
    tb = traceback.format_exc()
    diagnosis = diagnose_failure(context, exc, tb)
    print(f"DeepSeek diagnosis:\n{diagnosis}", file=sys.stderr)
    subject = f"Tipitaka job ERROR — {context}"
    body = (
        f"The daily Tipitaka translation job failed.\n\n"
        f"Context: {context}\n\n"
        f"{type(exc).__name__}: {exc}\n\n"
        f"{tb}\n"
        f"{'=' * 60}\n"
        f"DeepSeek diagnosis:\n\n{diagnosis}\n"
    )
    send_email_or_spool(
        account, spool_dir, subject, body,
        f"error report: {context}",
    )
