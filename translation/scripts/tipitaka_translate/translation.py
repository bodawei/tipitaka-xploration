"""Prompt assembly, direct model calls, and usage reporting.

A request is a plain dict {"system", "user", "hint"} so the same request can be
sent directly (`run`) or bundled into a provider batch (batch.submit).
"""
import json

from . import providers
from .prompts import GLOSSARY_INSTRUCTION, VERSE_NUMBER_INSTRUCTION
from .source import format_pali

TRANSLATION_TRUNCATION_HINT = (
    "Translation truncated: hit max_tokens output cap. "
    "Increase max_tokens or reduce CHUNK_SIZE."
)

SUMMARY_TRUNCATION_HINT = (
    "Section summary truncated: hit max_tokens output cap. "
    "The section may be too long to summarise in one call."
)


def glossary_block(glossary):
    if not glossary.strip():
        return ""
    return f"Established glossary (reuse these renderings):\n{glossary}\n\n"


def _heading_str(heading):
    return " — ".join(heading) if heading else "(untitled section)"


def translation_request(system, filename, heading, chunk, glossary=""):
    return {
        "system": system + VERSE_NUMBER_INSTRUCTION + GLOSSARY_INSTRUCTION,
        "user": (
            f"{glossary_block(glossary)}Text: {filename}\n"
            f"Section: {_heading_str(heading)}\n\n{format_pali(chunk)}"
        ),
        "hint": TRANSLATION_TRUNCATION_HINT,
    }


def summary_request(system, filename, heading, section_verses, glossary=""):
    """Condensed prose summary of a whole section (verse boundaries flattened)."""
    pali_prose = " ".join(" ".join(v["texts"]) for v in section_verses)
    return {
        "system": system,
        "user": (
            f"{glossary_block(glossary)}Text: {filename}\n"
            f"Section: {_heading_str(heading)}\n\n{pali_prose}"
        ),
        "hint": SUMMARY_TRUNCATION_HINT,
    }


def run(provider, request):
    """Send one request directly; returns (text, usage)."""
    return providers.chat_message(
        provider["name"], provider["key"], provider["model"],
        request["system"], request["user"], request["hint"],
    )


def _flatten(usage, prefix=""):
    for key, value in usage.items():
        if isinstance(value, dict):
            yield from _flatten(value, f"{prefix}{key}.")
        elif value is not None and not isinstance(value, list):
            yield f"{prefix}{key}", value


def usage_header(provider_name, model, usage, via):
    """Token usage for the top of an email: a readable summary line, then the
    `usage` object exactly as the API returned it, as raw JSON."""
    fields = ", ".join(f"{k}={v}" for k, v in _flatten(usage)) or "not reported"
    return (
        f"Usage [{provider_name} · {model} · {via}]: {fields}\n"
        f"Raw usage JSON: {json.dumps(usage, ensure_ascii=False)}"
    )
