"""Prompt assembly and model calls for translating and summarising text."""
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


def call_model(provider, api_key, model, system, filename, heading, chunk, glossary=""):
    heading_str = " — ".join(heading) if heading else "(untitled section)"
    user_msg = (
        f"{glossary_block(glossary)}Text: {filename}\n"
        f"Section: {heading_str}\n\n{format_pali(chunk)}"
    )
    return providers.chat_message(
        provider,
        api_key, model, system + VERSE_NUMBER_INSTRUCTION + GLOSSARY_INSTRUCTION,
        user_msg,
        TRANSLATION_TRUNCATION_HINT,
    )


def summarize_section(provider, api_key, model, system, filename, heading, section_verses, glossary=""):
    """Condensed prose summary of a whole section (verse boundaries flattened)."""
    heading_str = " — ".join(heading) if heading else "(untitled section)"
    pali_prose = " ".join(" ".join(v["texts"]) for v in section_verses)
    user_msg = (
        f"{glossary_block(glossary)}Text: {filename}\n"
        f"Section: {heading_str}\n\n{pali_prose}"
    )
    return providers.chat_message(
        provider,
        api_key, model, system, user_msg,
        SUMMARY_TRUNCATION_HINT,
    )
