#!/usr/bin/env python3
"""
Daily Tipitaka reading job.

Reads the next chunk of verses from the current position in a rotating list of
romn/*.mul.xml files, translates it to English via the Anthropic API, emails
the result via Resend, and advances the on-disk progress state.

A "verse" is a <p rend="bodytext" n="N"> element plus any immediately
following unnumbered <p rend="bodytext"> continuation paragraphs. A chunk
never crosses a chapter/kanda boundary (i.e. never spans two different
immediate parent <div> elements), even if that means fewer verses than
CHUNK_SIZE.
"""
import json
import os
import re
import sys
import traceback
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
RESEND_URL = "https://api.resend.com/emails"

WHITESPACE_RE = re.compile(r"\s+")

ENGLISH_SYSTEM = (
    "You are an expert translator of Pali Buddhist canonical texts (the Tipitaka), "
    "working from IAST Latin-transliterated Pali. Translate the passage into clear, "
    "faithful, readable English prose. Translate strictly verse by verse: output one "
    "entry per verse, each starting with its verse number in brackets like '[12]', "
    "followed only by the English translation of that verse. Preserve proper nouns "
    "(place names, personal names) transliterated sensibly. Do not add commentary, "
    "headers, or any text beyond the verse-by-verse translations."
)

CHINESE_SYSTEM = (
    "You are an expert translator of Pali Buddhist canonical texts (the Tipitaka), "
    "working from IAST Latin-transliterated Pali. Translate the passage into clear, "
    "faithful, readable Traditional Chinese (繁體中文) prose. Translate strictly verse "
    "by verse: output one entry per verse, each starting with its verse number in "
    "brackets like '[12]', followed only by the Traditional Chinese translation of that "
    "verse. Preserve proper nouns (place names, personal names) using established "
    "Chinese Buddhist renderings where they exist, otherwise transliterate sensibly. "
    "Use Traditional Chinese characters only, never Simplified. Do not add commentary, "
    "headers, or any text beyond the verse-by-verse translations."
)

# Ordered list of target languages. Each is translated from the same Pali chunk,
# archived to its own file (code-YYYY-MM-DD-runN.txt), and emailed separately.
LANGUAGES = [
    {"code": "eng", "label": "English", "system": ENGLISH_SYSTEM},
    {"code": "zh", "label": "Traditional Chinese", "system": CHINESE_SYSTEM},
]

GLOSSARY_MARKER = "=== NEW GLOSSARY TERMS ==="

# Appended to every language's system prompt so glossary behaviour is identical
# across languages. Keeps key-term renderings consistent from run to run.
GLOSSARY_INSTRUCTION = (
    "\n\nA glossary of key Pali terms established in previous runs may be provided "
    "with the passage. Whenever such a term appears, reuse its given rendering "
    "verbatim so translations stay consistent across runs. Separately, AFTER the "
    "verse-by-verse translation, if this passage contains key Pali terms that are "
    "doctrinally significant or genuinely hard to translate and are NOT already in "
    f"the provided glossary, output a line containing exactly '{GLOSSARY_MARKER}' "
    "and then one line per new term in the form 'pali | rendering | brief gloss', "
    "where 'rendering' is the exact wording you used in the target language. "
    "Include only genuinely key doctrinal or technical terms — never ordinary "
    "vocabulary or proper names (people, places). If there are no new key terms, "
    "omit the marker line entirely."
)


def normalize(text):
    return WHITESPACE_RE.sub(" ", text).strip()


def clean_text(elem):
    parts = [elem.text or ""]
    for child in elem:
        skip = child.tag == "note" or (
            child.tag == "hi" and child.get("rend") in ("paranum", "dot")
        )
        if not skip:
            parts.append(clean_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def starts_new_section(elem):
    """Whether `elem` is an intra-chapter section boundary marker.

    Sections within a single <div> are delimited by a subhead title for the
    new section (`<p rend="subhead">`) and/or a closing trailer for the
    previous one — a centred paragraph or <trailer> containing "niṭṭhit"
    (niṭṭhito / niṭṭhitaṃ, "is finished"). A chunk must not cross such a
    boundary, even though it does not coincide with a <div> boundary.
    """
    if elem.tag == "p" and elem.get("rend") == "subhead":
        return True
    if elem.tag == "trailer" or (elem.tag == "p" and elem.get("rend") == "centre"):
        return "niṭṭhit" in normalize(clean_text(elem))
    return False


def heading_path(div_node, parent_map):
    chain = []
    node = div_node
    while node is not None:
        if node.tag == "div":
            head = node.find("head")
            if head is not None and (head.text or "").strip():
                chain.append(head.text.strip())
        node = parent_map.get(node)
    chain.reverse()
    return chain


def parse_verses(filepath):
    tree = ET.parse(filepath)
    root = tree.getroot()
    parent_map = {child: parent for parent in root.iter() for child in parent}

    verses = []
    current = None
    section_pending = False
    for el in root.iter():
        if starts_new_section(el):
            section_pending = True
            continue
        if el.tag != "p" or el.get("rend") != "bodytext":
            continue
        n = el.get("n")
        text = normalize(clean_text(el))
        if n is not None:
            current = {
                "n": int(n),
                "parent": parent_map.get(el),
                "texts": [text],
                "section_start": section_pending,
            }
            verses.append(current)
            section_pending = False
        elif current is not None:
            current["texts"].append(text)

    for v in verses:
        v["heading"] = heading_path(v["parent"], parent_map)
    return verses


def select_chunk(files, source_dir, state, chunk_size):
    idx = files.index(state["file"]) if state["file"] in files else 0
    next_verse = state["next_verse"]

    for _ in range(len(files) + 1):
        filename = files[idx]
        verses = parse_verses(os.path.join(source_dir, filename))

        start_i = next((i for i, v in enumerate(verses) if v["n"] >= next_verse), None)
        if start_i is None:
            idx = (idx + 1) % len(files)
            next_verse = 1
            continue

        chunk = [verses[start_i]]
        parent = verses[start_i]["parent"]
        i = start_i + 1
        while (
            len(chunk) < chunk_size
            and i < len(verses)
            and verses[i]["parent"] is parent
            and not verses[i]["section_start"]
        ):
            chunk.append(verses[i])
            i += 1

        if i < len(verses):
            new_state = {"file": filename, "next_verse": verses[i]["n"]}
        else:
            new_idx = (idx + 1) % len(files)
            new_state = {"file": files[new_idx], "next_verse": 1}

        return filename, chunk, chunk[0]["heading"], new_state

    raise RuntimeError("No numbered verses found in any source file")


def format_pali(chunk):
    return "\n\n".join(f"[{v['n']}] {' '.join(v['texts'])}" for v in chunk)


def call_anthropic(api_key, model, system, filename, heading, chunk, glossary=""):
    heading_str = " — ".join(heading) if heading else "(untitled section)"
    glossary_block = (
        f"Established glossary (reuse these renderings):\n{glossary}\n\n"
        if glossary.strip()
        else ""
    )
    user_msg = f"{glossary_block}Text: {filename}\nSection: {heading_str}\n\n{format_pali(chunk)}"

    body = json.dumps(
        {
            "model": model,
            "max_tokens": 16000,
            "system": system + GLOSSARY_INSTRUCTION,
            "messages": [{"role": "user", "content": user_msg}],
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode("utf-8"))

    stop_reason = result.get("stop_reason")
    if stop_reason == "max_tokens":
        raise RuntimeError(
            "Translation truncated: hit max_tokens output cap. "
            "Increase max_tokens or reduce CHUNK_SIZE."
        )

    return "".join(block["text"] for block in result["content"] if block["type"] == "text")


def glossary_path(glossary_dir, code):
    return os.path.join(glossary_dir, f"glossary-{code}.md")


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


def send_email(api_key, email_from, email_to, subject, text_body):
    body = json.dumps(
        {"from": email_from, "to": [email_to], "subject": subject, "text": text_body}
    ).encode("utf-8")
    req = urllib.request.Request(
        RESEND_URL,
        data=body,
        method="POST",
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
            "user-agent": "tipitaka-xploration-daily-translate/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise urllib.error.HTTPError(exc.url, exc.code, f"{exc.reason}: {detail}", exc.headers, None) from None


def next_run_number(translation_dir, date_str):
    codes = "|".join(re.escape(lang["code"]) for lang in LANGUAGES)
    pattern = re.compile(rf"^(?:pali|full|{codes})-{re.escape(date_str)}-run(\d+)\.txt$")
    max_run = 0
    if os.path.isdir(translation_dir):
        for name in os.listdir(translation_dir):
            m = pattern.match(name)
            if m:
                max_run = max(max_run, int(m.group(1)))
    return max_run + 1


def write_translation_files(translation_dir, date_str, run_n, header, translations, pali_text):
    os.makedirs(translation_dir, exist_ok=True)
    suffix = f"{date_str}-run{run_n}.txt"

    with open(os.path.join(translation_dir, f"pali-{suffix}"), "w", encoding="utf-8") as f:
        f.write(f"{header}\n{pali_text}\n")

    for code, translation in translations.items():
        with open(os.path.join(translation_dir, f"{code}-{suffix}"), "w", encoding="utf-8") as f:
            f.write(f"{header}\n{translation}\n")

    with open(os.path.join(translation_dir, f"full-{suffix}"), "w", encoding="utf-8") as f:
        f.write(f"{header}\n{translations['eng']}\n\n")
        for lang in LANGUAGES:
            if lang["code"] == "eng":
                continue
            f.write(f"{'-' * 40}\n{lang['label']}:\n\n{translations[lang['code']]}\n\n")
        f.write(f"{'-' * 40}\nOriginal (Pali, IAST):\n\n{pali_text}\n")


def send_error_report(api_key, email_from, email_to, context, exc):
    subject = f"Tipitaka job ERROR — {context}"
    body = (
        f"The daily Tipitaka translation job failed.\n\n"
        f"Context: {context}\n\n"
        f"{type(exc).__name__}: {exc}\n\n"
        f"{traceback.format_exc()}"
    )
    send_email(api_key, email_from, email_to, subject, body)


def main():
    source_dir = os.environ.get("SOURCE_DIR", "romn")
    state_path = os.environ.get("STATE_FILE", "translation/state/translation-progress.json")
    translation_dir = os.environ.get("TRANSLATION_DIR", "translation/results")
    glossary_dir = os.environ.get("GLOSSARY_DIR", "translation")
    chunk_size = int(os.environ.get("CHUNK_SIZE", "20"))
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
    dry_run = os.environ.get("DRY_RUN") == "1"

    context = "startup"
    try:
        files = sorted(f for f in os.listdir(source_dir) if f.endswith(".mul.xml"))
        if not files:
            raise RuntimeError(f"No *.mul.xml files found in {source_dir}")

        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
        context = f"file={state.get('file')}, next_verse={state.get('next_verse')}"

        filename, chunk, heading, new_state = select_chunk(files, source_dir, state, chunk_size)
        verse_numbers = [v["n"] for v in chunk]
        verse_range = (
            str(verse_numbers[0])
            if len(verse_numbers) == 1
            else f"{verse_numbers[0]}-{verse_numbers[-1]}"
        )
        heading_str = " — ".join(heading) if heading else filename
        context = f"{filename} verses {verse_range}"

        print(f"Translating {filename} verses {verse_range} ({heading_str}), {len(chunk)} verse(s)")

        header = f"{heading_str}\n{filename}, verses {verse_range}\n"
        pali_text = format_pali(chunk)

        if dry_run:
            translation = "[DRY RUN — translation skipped]"
            print(f"{header}\n{translation}")
            return

        glossaries = {
            lang["code"]: read_glossary(glossary_path(glossary_dir, lang["code"]))
            for lang in LANGUAGES
        }

        translations = {}
        new_terms = {}
        for lang in LANGUAGES:
            code = lang["code"]
            print(f"Translating to {lang['label']}...")
            response = call_anthropic(
                os.environ["ANTHROPIC_API_KEY"], model, lang["system"], filename,
                heading, chunk, glossary=glossaries[code][0],
            )
            translations[code], new_terms[code] = split_translation_and_terms(response)

            label_suffix = "" if code == "eng" else f" ({lang['label']})"
            subject = f"Tipitaka reading{label_suffix}: {filename} verses {verse_range} — {heading_str}"
            send_email(
                os.environ["RESEND_API_KEY"],
                os.environ["EMAIL_FROM"],
                os.environ["EMAIL_TO"],
                subject,
                f"{header}\n{translations[code]}",
            )

        date_str = datetime.now(timezone.utc).date().isoformat()
        run_n = next_run_number(translation_dir, date_str)
        write_translation_files(translation_dir, date_str, run_n, header, translations, pali_text)

        for lang in LANGUAGES:
            code = lang["code"]
            append_glossary(
                glossary_path(glossary_dir, code), lang["label"],
                new_terms[code], glossaries[code][1],
            )

        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(new_state, f, indent=2)
            f.write("\n")

        print(f"State updated: {new_state}")

    except Exception as exc:
        print(f"ERROR during {context}: {exc}", file=sys.stderr)
        traceback.print_exc()
        if not dry_run:
            try:
                send_error_report(
                    os.environ["RESEND_API_KEY"],
                    os.environ["EMAIL_FROM"],
                    os.environ["EMAIL_TO"],
                    context,
                    exc,
                )
            except Exception as report_exc:
                print(f"Additionally failed to send error report: {report_exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
