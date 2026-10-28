"""Glossary files: reading established terms, extracting new ones, appending.

Each provider-and-language pair has its own Markdown table of
`Pali | rendering | notes` rows, at translation/glossary-<provider>-<code>.md.
The file is both a human-readable record and a consistency anchor injected
into every request.
"""
import os

from .prompts import GLOSSARY_MARKER, LANGUAGES


def glossary_path(glossary_dir, provider, code):
    return os.path.join(glossary_dir, f"glossary-{provider}-{code}.md")


def read_glossary(path):
    """Return (raw_markdown, set_of_casefolded_pali_keys) for an existing glossary.

    Missing file -> ("", set()). Keys are the first cell of each table body row,
    skipping the '| Pali | ... |' header and the '| --- | --- |' separator.
    """
    if not os.path.exists(path):
        return "", set()
    with open(path, encoding="utf-8") as f:
        text = f.read()

    keys = set()
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or not cells[0]:
            continue
        first = cells[0]
        if first.lower() == "pali" or set(first) <= set("-: "):
            continue
        keys.add(first.casefold())
    return text, keys


def load_glossaries(active, glossary_dir):
    """Read every active provider's glossary for every language.

    Returns {(provider_name, language_code): (raw_markdown, existing_keys)}.
    """
    return {
        (p["name"], lang["code"]): read_glossary(
            glossary_path(glossary_dir, p["name"], lang["code"])
        )
        for p in active
        for lang in LANGUAGES
    }


def split_translation_and_terms(response):
    """Split a model response into (translation_text, [(pali, rendering, gloss), ...]).

    Everything before the GLOSSARY_MARKER line is the verse translation; anything
    after is parsed as pipe-delimited new-term rows.
    """
    body, sep, rest = response.partition(GLOSSARY_MARKER)
    translation = body.strip()
    terms = []
    if sep:
        for line in rest.splitlines():
            line = line.strip().strip("|").strip()
            if not line or "|" not in line:
                continue
            cells = [c.strip() for c in line.split("|")]
            pali = cells[0]
            if not pali or pali.lower() == "pali" or set(pali) <= set("-: "):
                continue
            rendering = cells[1] if len(cells) > 1 else ""
            gloss = cells[2] if len(cells) > 2 else ""
            terms.append((pali, rendering, gloss))
    return translation, terms


def append_glossary(path, label, new_terms, existing_keys):
    """Append genuinely-new terms to the glossary file, creating it if needed."""
    seen = set(existing_keys)
    rows = []
    for pali, rendering, gloss in new_terms:
        key = pali.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append(f"| {pali} | {rendering} | {gloss} |")

    if not rows:
        return

    new_file = not os.path.exists(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        if new_file:
            f.write(
                f"# {label} glossary\n\n"
                "Key Pali terms and their established renderings, accumulated by the\n"
                "daily translation job to keep word choices consistent across runs.\n"
                "Managed automatically — new terms are appended as they first appear.\n\n"
                f"| Pali | {label} | Notes |\n| --- | --- | --- |\n"
            )
        f.write("\n".join(rows) + "\n")
