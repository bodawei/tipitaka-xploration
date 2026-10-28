"""Prompt text and target-language definitions for the daily translation job.

Everything in this module is static content: the system prompts sent to the
models, the per-language registry, and the instructions appended to every
request. Keeping it separate makes the prompts easy to find and edit without
wading through the job's plumbing.
"""

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

ENGLISH_SUMMARY_SYSTEM = (
    "You are an expert translator and abridger of Pali Buddhist canonical texts "
    "(the Tipitaka), working from IAST Latin-transliterated Pali. You are given the "
    "full Pali text of one complete section. Produce a single condensed English "
    "prose summary of the whole section: flowing paragraphs, NOT verse by verse, "
    "with no verse numbers. Convey the essential narrative and doctrinal content "
    "faithfully but very concisely — the summary should be roughly one tenth the "
    "length of the source (about a 90% reduction). Preserve proper nouns (place "
    "names, personal names) transliterated sensibly. Output only the summary prose, "
    "with no headers, labels, or commentary."
)

CHINESE_SUMMARY_SYSTEM = (
    "You are an expert translator and abridger of Pali Buddhist canonical texts "
    "(the Tipitaka), working from IAST Latin-transliterated Pali. You are given the "
    "full Pali text of one complete section. Produce a single condensed Traditional "
    "Chinese (繁體中文) prose summary of the whole section: flowing paragraphs, NOT "
    "verse by verse, with no verse numbers. Convey the essential narrative and "
    "doctrinal content faithfully but very concisely — the summary should be roughly "
    "one tenth the length of the source (about a 90% reduction). Preserve proper "
    "nouns (place names, personal names) using established Chinese Buddhist renderings "
    "where they exist, otherwise transliterate sensibly. Use Traditional Chinese "
    "characters only, never Simplified. Output only the summary prose, with no "
    "headers, labels, or commentary."
)

# Ordered list of target languages. Each is translated from the same Pali chunk,
# archived to its own file (code-YYYY-MM-DD-runN.txt), and emailed separately.
# When a chunk completes a section, `summary_system` drives a condensed prose
# summary of the whole section, which is emailed (not archived).
LANGUAGES = [
    {
        "code": "eng",
        "label": "English",
        "system": ENGLISH_SYSTEM,
        "summary_system": ENGLISH_SUMMARY_SYSTEM,
    },
    {
        "code": "zh",
        "label": "Traditional Chinese",
        "system": CHINESE_SYSTEM,
        "summary_system": CHINESE_SUMMARY_SYSTEM,
    },
]

# Appended to every language's translation prompt. A chunk may contain an
# abbreviated passage whose number is a range (see source.parse_verse_number),
# and the model must not try to tidy that away.
VERSE_NUMBER_INSTRUCTION = (
    "\n\nA verse number may be a range, like '[225-240]', where one abbreviated "
    "passage stands in for a run of elided verses. Translate such a passage as "
    "the single entry it is, and echo its number back exactly as given, range "
    "and all — never split it into separate verses and never renumber it."
)

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
