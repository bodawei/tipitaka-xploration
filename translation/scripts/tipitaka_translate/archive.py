"""Writing the per-run archive files under translation/results/<provider>/."""
import os
import re

from .prompts import LANGUAGES


def next_run_number(translation_dir, date_str):
    prefixes = ["pali", "full"]
    for lang in LANGUAGES:
        prefixes += [lang["code"], f"summary-{lang['code']}"]
    alt = "|".join(re.escape(p) for p in prefixes)
    pattern = re.compile(rf"^(?:{alt})-{re.escape(date_str)}-run(\d+)\.txt$")
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


def write_summary_files(translation_dir, date_str, run_n, header, summaries):
    os.makedirs(translation_dir, exist_ok=True)
    suffix = f"{date_str}-run{run_n}.txt"
    for code, summary in summaries.items():
        with open(os.path.join(translation_dir, f"summary-{code}-{suffix}"), "w", encoding="utf-8") as f:
            f.write(f"{header}\n{summary}\n")
