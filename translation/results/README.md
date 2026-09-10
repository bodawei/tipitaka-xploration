# Daily translation archive

Populated by the daily translation job (see `.github/workflows/daily-translation.yml`
and `translation/scripts/daily_translate.py`). Each successful run writes several files, named
by UTC date and a per-day run counter:

- `pali-YYYY-MM-DD-runN.txt` — the original Pali text of that day's chunk.
- `eng-YYYY-MM-DD-runN.txt` — the English translation only (matches the emailed content).
- `zh-YYYY-MM-DD-runN.txt` — the Traditional Chinese translation only (matches the emailed content).
- `full-YYYY-MM-DD-runN.txt` — heading, English translation, Traditional Chinese translation, and original Pali together.
- `summary-<code>-YYYY-MM-DD-runN.txt` — only on runs that complete a section: a
  condensed prose summary (~90% shorter) of the whole just-finished section, one
  per target language (matches the emailed summary). Covers the entire section,
  which may span several days' chunks — not just that run's chunk.

Each target language is translated from the same Pali chunk and emailed as its own
separate message; section summaries are likewise emailed per language.

`runN` starts at `run1` for the first run on a given UTC date and increments for
any additional runs that day (e.g. a manual re-run).
