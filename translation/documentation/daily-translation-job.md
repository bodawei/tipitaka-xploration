# Daily Tipitaka translation job

A GitHub Actions job that works through the Pali canon a chunk at a time: each
day it translates the next chunk of verses from the current file to English,
emails the translation, archives the day's output, and advances its saved
position for next time.

## Flow

1. Load `translation/state/translation-progress.json` — `{"file": "<name>.mul.xml", "next_verse": N}`.
2. Parse that file (`romn/<name>.mul.xml`) and select the next chunk of verses
   starting at `next_verse` (see "Verse and chunk model" below).
3. For each target language, read its glossary
   (`translation/glossary-<code>.md`, e.g. `glossary-eng.md`) and include it in
   that language's Anthropic request, so key Pali terms are rendered the same way
   as in earlier runs (see "Glossaries" below).
4. Translate the chunk's Pali text into each target language via the Anthropic
   API. Each response also reports any new key terms it introduced.
5. Email each translation separately via the Resend API.
6. Write the archive files to `translation/results/` (Pali only, one per
   target language, and a combined record — see `translation/results/README.md`),
   and append any newly-introduced key terms to each `translation/glossary-<code>.md`.
7. Update `translation/state/translation-progress.json` to point at the verse after the
   last one translated (or the start of the next file, if the chunk reached
   the end of the current one).
8. The GitHub Actions workflow commits and pushes the updated state, new archive
   files, and any glossary additions back to the repo.

Steps 3–7 are atomic: if anything fails partway, nothing from that run is
kept — no partial archive files, no glossary additions, no state advance — so the
same chunk is simply retried on the next run. See "Error handling" below.

## Verse and chunk model

A **verse** is a `<p rend="bodytext" n="N">` element plus any immediately
following unnumbered `<p rend="bodytext">` continuation paragraphs. Verse
numbers increase monotonically through a file with no resets.

A **chunk** is up to `CHUNK_SIZE` consecutive verses (default 20) starting
from the saved position, but it never crosses a section boundary — so it may
contain fewer than `CHUNK_SIZE` verses. A chunk stops before a verse that:

- has a different immediate parent `<div>` (a chapter/kanda boundary), or
- begins a new intra-chapter section — marked by a new-section subhead
  (`<p rend="subhead">`) and/or a closing trailer for the previous section
  (a centred paragraph or `<trailer>` containing "niṭṭhit", e.g.
  "Sudinnabhāṇavāro niṭṭhito."). These markers sit between verses within a
  single `<div>`, so they are detected in document order rather than by the
  parent-`<div>` check.

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

## File and directory layout

- `translation/scripts/daily_translate.py` — the job's entire logic (stdlib-only
  Python, no pip install required).
- `translation/state/translation-progress.json` — current position (file + next verse).
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
`chunk_size` override and a `dry_run` toggle that skips the Anthropic call,
the email, the archive files, the glossary updates, and the state update — it
only logs the selected chunk, for safe testing.

## Error handling

If translation, emailing, file-writing, or the state update fails at any
point, the job sends a separate error-report email instead (subject
`Tipitaka job ERROR — <context>`, containing the exception and a truncated
traceback) and exits non-zero. If the error-report email itself can't be
sent (e.g. broken email secrets), the error is printed to the job log and the
run still exits non-zero, so the failure is visible as a red run in the
Actions tab even in that fallback case.

## Known assumptions

- Dates and run numbering (`run1`, `run2`, ...) use UTC, matching the cron
  schedule.
- The email body contains the English translation only; the Pali text and
  the combined record are archived on disk in `translation/results/`, not emailed.
- File ordering is alphabetical across all `romn/*.mul.xml` files, not a
  curated canonical reading order — it starts at `vin01m.mul.xml` and wraps
  around after the last file.
