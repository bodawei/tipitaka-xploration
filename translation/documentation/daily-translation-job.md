# Daily Tipitaka translation job

A GitHub Actions job that works through the Pali canon a chunk at a time: each
day it translates the next chunk of verses from the current file to English,
emails the translation, archives the day's output, and advances its saved
position for next time.

## Flow

1. Load `translation/state/translation-progress.json` — `{"file": "<name>.mul.xml", "next_verse": N}`.
2. Retry any emails an earlier run could not send (see "Undeliverable email"
   below), before starting any work of this run's own.
3. Parse that file (`romn/<name>.mul.xml`) and select the next chunk of verses
   starting at `next_verse` (see "Verse and chunk model" below).
4. For each target language, read its glossary
   (`translation/glossary-<code>.md`, e.g. `glossary-eng.md`) and include it in
   that language's Anthropic request, so key Pali terms are rendered the same way
   as in earlier runs (see "Glossaries" below).
5. Translate the chunk's Pali text into each target language via the Anthropic
   API. Each response also reports any new key terms it introduced.
6. Email each translation separately via the Resend API.
7. If this chunk completed a section (see "Section summaries" below), gather the
   whole just-finished section's Pali and email a condensed prose summary in each
   target language.
8. Write the archive files to `translation/results/` (Pali only, one per
   target language, a combined record, and — when a section completed — a
   `summary-<code>` file per language; see `translation/results/README.md`),
   and append any newly-introduced key terms to each `translation/glossary-<code>.md`.
9. Update `translation/state/translation-progress.json` to point at the verse after the
   last one translated (or the start of the next file, if the chunk reached
   the end of the current one).
10. The GitHub Actions workflow commits and pushes the updated state, new archive
    files, and any glossary additions back to the repo.

Steps 4–9 are atomic: if anything fails partway, nothing from that run is
kept — no partial archive files, no glossary additions, no state advance — so the
same chunk is simply retried on the next run. Email delivery is the one
exception: it is retried out of a spool rather than rolling the run back (see
"Undeliverable email"). See "Error handling" below.

## Verse and chunk model

A **verse** is a `<p rend="bodytext" n="N">` element plus any immediately
following unnumbered `<p rend="bodytext">` continuation paragraphs. Verse
numbers increase monotonically through a file with no resets.

`N` is usually a single number, but where an abbreviated passage stands in for
a run of elided verses it is a **range**, e.g. `n="225-240"`. Such a paragraph
is one verse as far as the job is concerned — it is never split — but it counts
for its whole span, and it is labelled by that range (`[225-240]`) in the Pali,
the prompts, the archives and the email subjects. The translation prompt tells
the model to echo a range back as given rather than expanding or renumbering
it. Ranges are common in the Saṃyutta and Yamaka files and can be very large:
`s0403m1.mul.xml` has a single paragraph numbered `308-1151`, 844 verses of
about 450 characters.

A **chunk** is up to `CHUNK_SIZE` consecutive verses (default 20) starting
from the saved position, but it never crosses a section boundary — so it may
contain fewer than `CHUNK_SIZE` verses. The budget counts verse numbers rather
than paragraphs, so a range counts for its span. Because a range is never
split, adding one may take a chunk **over** `CHUNK_SIZE`; that is accepted, and
the next chunk simply starts after the end of the range. A chunk stops before a
verse that:

- has a different immediate parent `<div>` (a chapter/kanda boundary), or
- begins a new intra-chapter section — marked by a heading for the new section
  and/or a closing trailer for the previous one. These markers sit between
  verses within a single `<div>`, so they are detected in document order rather
  than by the parent-`<div>` check.

Both markers come in several equivalent spellings, and any one of them is
enough to mark the boundary:

- **New-section heading** — a `<p>` whose `rend` is any of the CSCD heading
  styles: `nikaya`, `book`, `chapter`, `title`, `subhead`, `subsubhead`. The
  markup picks whichever rank suits the level being opened, so `subhead` alone
  is not sufficient — "3. Tatiyapārājikaṃ", which opens a section, is a
  `title`.
- **Closing trailer** — a centred paragraph (`<p rend="centre">`) or a
  `<trailer>` containing either "niṭṭhit" (niṭṭhito / niṭṭhitaṃ, "is
  finished", e.g. "Sudinnabhāṇavāro niṭṭhito.") or "samatt" (samatto /
  samattaṃ, "is completed", e.g. "Dutiyapārājikaṃ samattaṃ."). The two
  formulae are interchangeable.

**File order**: all `romn/*.mul.xml` files, sorted alphabetically, treated as
a circular list. Progress is seeded to start at `vin01m.mul.xml`, verse 1.
When the alphabetically-last file is exhausted, the job wraps back around to
the alphabetically-first file.

## Glossaries

Each target language keeps a running glossary of key Pali terms at
`translation/glossary-<code>.md` — a Markdown table of `Pali | rendering | notes`
rows. Its purpose is twofold: a human-readable guide to how doctrinally loaded
terms are being translated, and a consistency anchor so the same Pali word is
rendered the same way from one run to the next.

Each run injects the current glossary into that language's translation request.
The model reuses the listed renderings and, after the verse translation, reports
any key terms it introduced that are not yet in the glossary (doctrinal or
technical terms only — never ordinary vocabulary or proper names). Those new
terms are appended to the file on a successful run; entries are never rewritten
or removed automatically. The files start empty and are created on first use.

The directory is set by `GLOSSARY_DIR` (default `translation`).

## Section summaries

A chunk never crosses a section boundary, so whenever a chunk ends exactly at
one — an intra-chapter section break, a chapter/kanda `<div>` boundary, or the
end of a file — that chunk has completed a whole section. When that happens, the
job gathers the entire just-finished section's Pali (walking back to the
section's first verse, which may lie in an earlier run's chunk) and, for each
target language, emails a single condensed **prose** summary: verse boundaries
are flattened and the text is abridged to roughly one tenth its length (~90%
reduction). The current glossary is supplied for term consistency, but summaries
do not themselves add glossary entries.

Each summary is emailed and also archived to
`translation/results/summary-<code>-YYYY-MM-DD-runN.txt` (sharing the run's date
and run number). On a chunk that stops mid-section (because it hit `CHUNK_SIZE`),
no summary is produced.

### Backfilling a missed summary

If a section boundary went undetected, its summary was never sent and the job
has since read past it. Setting `SUMMARIZE_VERSE` to a verse number (or
`<file>.mul.xml:<verse>`) runs a summary-only pass over the whole section
containing that verse: it emails and archives the summaries exactly as a normal
run would, but translates nothing, adds no glossary terms, and leaves the saved
position untouched. It is also exposed as the `summarize_verse` input on the
manual `workflow_dispatch` run.

## Undeliverable email

A failed send does **not** abort the run. The translation has already been made
and archived, so rolling back would only re-translate and re-send the same
chunk; instead the exact message is written to
`translation/state/pending-email/email-<UTC timestamp>-<NNN>.json` — subject,
body, the originating context, an attempt count and the last error. The
recipient is deliberately not stored: it is a secret, and resolving it from the
environment at send time means a retry honours its current value. Each spooled
message is also logged as a GitHub Actions warning, so the run shows the problem
without going red.

At the start of every run, before any parsing or translation, the spool is
replayed oldest-first. A message that goes through is deleted; the first one
that fails again has its attempt count and error updated and the replay stops
there, leaving the rest queued in order for the next run rather than hammering a
service that is still down.

This applies to every outbound message — the per-language readings, the section
summaries, and the error reports themselves.

Two things make the spool durable, since each Actions run starts from a fresh
checkout and anything uncommitted is thrown away:

- the workflow's commit step adds all of `translation/state/`, not just the
  progress file, so spooled messages (and their later deletion) are committed;
- that step runs with `if: !cancelled()`, so a spooled message still gets
  committed on a run that failed for some other reason.

The spool directory is set by `EMAIL_SPOOL_DIR` (default
`translation/state/pending-email`).

## File and directory layout

- `translation/scripts/daily_translate.py` — the job's entire logic (stdlib-only
  Python, no pip install required).
- `translation/state/translation-progress.json` — current position (file + next verse).
- `translation/state/pending-email/` — emails that failed to send, awaiting
  retry (see "Undeliverable email"). Empty in the normal case.
- `translation/results/` — daily archive output (`pali-`, `eng-`, `zh-`, `full-`
  files per run; see `translation/results/README.md`).
- `translation/glossary-<code>.md` — per-language key-term glossary (e.g.
  `glossary-eng.md`, `glossary-zh.md`); see "Glossaries" below.
- `.github/workflows/daily-translation.yml` — the scheduled workflow.

## Configuration

Set these in the repo's Settings → Secrets and variables → Actions:

**Secrets**
- `ANTHROPIC_API_KEY` — used to call the Anthropic API for translation.
- `RESEND_API_KEY` — used to send email via the Resend API.
- `EMAIL_TO` — destination address for both the daily reading and any error reports.

**Variables**
- `EMAIL_FROM` — sender address (must be on a domain verified in Resend).
- `CHUNK_SIZE` (optional, default `20`) — verses per day.
- `ANTHROPIC_MODEL` (optional, default `claude-sonnet-5`).

**Repo setting**: Settings → Actions → General → Workflow permissions must be
set to "Read and write permissions" so the job's `GITHUB_TOKEN` can push its
own commits.

The workflow can also be run manually (`workflow_dispatch`) with an optional
`chunk_size` override, a `dry_run` toggle that skips the Anthropic call,
the email, the archive files, the glossary updates, and the state update — it
only logs the selected chunk, for safe testing — and a `summarize_verse` input
that backfills a missed section summary (see "Backfilling a missed summary").

## Error handling

If translation, file-writing, or the state update fails at any
point, the job sends a separate error-report email instead (subject
`Tipitaka job ERROR — <context>`, containing the exception and a truncated
traceback) and exits non-zero. If the error-report email itself can't be
sent (e.g. broken email secrets), the report is spooled for retry like any
other message, and the run still exits non-zero, so the failure is visible as
a red run in the Actions tab even in that fallback case.

A send failure on its own is not one of these errors: it is spooled and
retried, and the run completes successfully.

## Known assumptions

- Dates and run numbering (`run1`, `run2`, ...) use UTC, matching the cron
  schedule.
- Each run emails one message per target language (the verse-by-verse
  translation), plus, when a section completes, one condensed prose summary per
  language. The Pali text and the combined record are archived on disk in
  `translation/results/` but not emailed; the per-language translations and
  section summaries are both emailed and archived.
- File ordering is alphabetical across all `romn/*.mul.xml` files, not a
  curated canonical reading order — it starts at `vin01m.mul.xml` and wraps
  around after the last file.
