# Daily translation archive

Populated by the daily translation job (see `.github/workflows/daily-translation.yml`
and `translation/scripts/daily_translate.py`). Archives live in per-provider
subdirectories (`claude/`, `kimi/`, `deepseek/`, …) so the providers' output
can be compared side by side; each enabled provider gets its own complete copy
of every run's files. Providers are switched on and off with the booleans in
`translation/state/providers.json`. Each successful run writes several files,
named by the UTC date of the run that finished the chunk — with batching, the run
after the one that selected it — and a per-day run counter:

- `pali-YYYY-MM-DD-runN.txt` — the original Pali text of that day's chunk.
- `eng-YYYY-MM-DD-runN.txt` — the English translation only (matches the emailed content,
  including its opening lines: the token usage the API reported for that request,
  summarised and as raw JSON).
- `zh-YYYY-MM-DD-runN.txt` — the Traditional Chinese translation only (matches the emailed
  content, usage lines included).
- `full-YYYY-MM-DD-runN.txt` — heading, English translation, Traditional Chinese translation, and original Pali together.
- `summary-<code>-YYYY-MM-DD-runN.txt` — only on runs that complete a section: a
  condensed prose summary (~90% shorter) of the whole just-finished section, one
  per target language (matches the emailed summary, usage lines included). Covers the entire section,
  which may span several days' chunks — not just that run's chunk.

All enabled providers translate the same Pali chunk each run (see "Providers"
in `translation/documentation/daily-translation-job.md`); each target language is
translated per provider and emailed as its own separate message with the provider
tagged in the subject; section summaries are likewise emailed per provider per
language.

`runN` starts at `run1` for the first run on a given UTC date and increments for
any additional runs that day (e.g. a manual re-run), counted independently per
provider.

Verse numbers in these files are usually single numbers, but an abbreviated
passage standing in for a run of elided verses is labelled with its whole range
(e.g. `[225-240]`) — one entry, not sixteen.
