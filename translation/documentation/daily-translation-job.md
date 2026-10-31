# Daily Tipitaka translation job

A GitHub Actions job that works through the Pali canon a chunk at a time: each
day it translates the next chunk of verses from the current file, emails the
translations, archives the day's output, and advances its saved position for
next time.

The job can run several **providers** (currently `claude`, `kimi`, and
`deepseek`), each an independent large-language-model API. Every enabled
provider translates the same chunk on the same day without consulting the
others, so their output can be compared side by side. Each provider keeps its
own progress state, glossary, and result archive; emails are sent per provider
with the provider tagged in the subject.

## Flow

Providers with a batch API (`claude`, `kimi`) are sent each chunk as a
discounted asynchronous **batch**, so a chunk normally takes two runs: it is
submitted on one day and finished on the next (see "Batching" below). Each run:

1. Read the provider on/off booleans from
   `translation/state/providers.json` (see "Providers" below). If every
   provider is switched off and no batch is pending, stop here without
   translating, emailing, or updating anything. An optional `PROVIDERS` run
   override can narrow the enabled set further.
2. Load each enabled provider's saved position —
   `translation/state/translation-progress-<provider>.json`, each
   `{"file": "<name>.mul.xml", "next_verse": N}` — plus that of any provider
   with a pending batch. The positions must agree (the providers run in
   lockstep), so a disagreement aborts the run with an error rather than
   producing a mismatched comparison.
3. Retry any emails an earlier run could not send (see "Undeliverable email"
   below), before starting any work of this run's own.
4. **Collect.** If `translation/state/pending-batch.json` records a batch
   submitted by an earlier run, ask each batch provider whether it has
   finished. If any is still running, stop; the next run checks again. Once
   all have finished:
   1. Fetch every batch result. If any request failed or expired, discard the
      pending batch, send an error report, and exit non-zero; the next run
      submits the same chunk again.
   2. Re-select the pending chunk from the position recorded with it, and
      translate it directly with every enabled provider that was not in the
      batch (`deepseek`, which has no batch API).
   3. **Finish the chunk** for every provider together: email each
      translation separately via the Resend API, tagged by provider
      (`Tipitaka reading [claude]: …`, `Tipitaka reading [deepseek]
      (Traditional Chinese): …`, and so on); if the chunk completed a section
      (see "Section summaries"), email the condensed prose summaries too;
      write the archive files under `translation/results/<provider>/` (see
      `translation/results/README.md`); append newly-introduced key terms to
      each provider's glossaries; and update every provider's state file to
      point at the verse after the chunk (or the start of the next file, if
      the chunk reached the end of the current one). Then delete the
      pending-batch record.
5. **Submit.** Parse the file at the (possibly just-advanced) position
   (`romn/<name>.mul.xml`) and select the next chunk of verses (see "Verse and
   chunk model" below). Build each batch provider's requests — one
   translation per target language, each carrying that provider's glossary
   for the language (see "Glossaries" below), plus one summary per language
   when the chunk completes a section — and submit them as one batch per
   provider. Record the batch ids in `pending-batch.json` and exit.
6. The GitHub Actions workflow commits and pushes the updated states, the
   pending-batch record, new archive files, and any glossary additions back
   to the repo.

If no batch provider is enabled, or the run sets `USE_BATCH=0`, step 5
instead translates the chunk directly with every enabled provider and
finishes it (step 4.3) in the same run.

Every email in a run opens with the token usage the API reported for the
request that produced it: a readable line naming the provider, the model, and
whether it came through the batch API (with its discount) or the direct API,
followed by the provider's `usage` object exactly as returned, as raw JSON:

```
Usage [claude · claude-opus-5 · batch API, 50% off]: input_tokens=8412, output_tokens=3906, ...
Raw usage JSON: {"input_tokens": 8412, "output_tokens": 3906, ...}
```

Finishing a chunk is atomic: if anything fails partway, nothing is kept — no
partial archive files, no glossary additions, no state advance. A pending
batch is kept, so the next run collects it again (results stay available for
29 days) and retries the direct providers. Email delivery is the one
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

## Providers

A provider is one LLM API integration: `claude` (Anthropic Messages API),
`kimi` (Moonshot chat-completions API), and `deepseek` (DeepSeek
chat-completions API) are built in; the registry lives in
`translation/scripts/tipitaka_translate/config.py`, and the API clients and
dispatch in `translation/scripts/tipitaka_translate/providers.py`. Each
provider carries its own API-key and model environment variables:

- `claude` — `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` (default `claude-sonnet-5`;
  surrounding whitespace in any model variable is ignored)
- `kimi` — `KIMI_API_KEY`, `KIMI_MODEL` (default `kimi-k2.6`)
- `deepseek` — `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL` (default `deepseek-flash`)

### Turning providers on and off

`translation/state/providers.json` holds one boolean per provider. Edit it to
switch providers on or off; the file is committed by the workflow like the
progress states, so a change takes effect on the next run without touching
repository variables or secrets:

```json
{
  "claude": true,
  "kimi": true,
  "deepseek": true
}
```

Only providers set to `true` run. A provider absent from the file is treated
as off, and an unknown name is rejected so a typo cannot silently disable the
job. If every provider is `false`, the script logs that everything is
disabled and exits successfully without translating, emailing, retrying the
mail spool, or changing any state or archive. The file is set by
`PROVIDERS_FILE` (default `translation/state/providers.json`).

The optional `PROVIDERS` environment variable (whitespace-separated names, set
by the `providers` input on a manual run) narrows the enabled set for that run
only. It cannot switch on a provider that is `false` in `providers.json`.

All enabled providers translate the **same** chunk, selected from their
shared saved position, and the run succeeds only if every provider succeeded —
so the providers advance in lockstep and a day's archives always line up for
comparison.

Pausing a provider (e.g. while it is having an outage) advances only the
remaining providers' states. When you later re-enable the paused provider, the
startup lockstep check will refuse to run until the positions agree again —
finish the catch-up with single-provider runs, or edit the state files by
hand.

## Batching

`claude` uses the Anthropic Message Batches API (50% off every token) and
`kimi` the Moonshot batch API (40% off). A batch usually finishes well within
an hour, but may take up to 24 hours before it expires, so the job never waits
for one: it submits a chunk and collects the results on the next run. The
emails and archives for a chunk therefore arrive one run after it is selected,
and are dated by the run that finished it. On the very first run after
batching is turned on, a chunk is submitted but nothing is emailed.

`deepseek` has no batch API. It is called directly, but only at collection
time, on the same chunk, so that every provider's state advances together.
(DeepSeek's off-peak rates are already half price, and the scheduled run time
falls outside its peak hours.)

The record of a submitted batch is `translation/state/pending-batch.json` (set
by `PENDING_BATCH_FILE`):

```json
{
  "submitted_at": "2026-11-01T13:05:12Z",
  "position": {"file": "vin01m.mul.xml", "next_verse": 180},
  "chunk_size": 10,
  "file": "vin01m.mul.xml",
  "verse_range": "180-189",
  "batches": {"claude": {"id": "msgbatch_…", "model": "claude-sonnet-5"}}
}
```

The chunk itself is not stored: collection re-selects it from `position` and
`chunk_size`, and refuses to continue if that no longer yields the recorded
file and verse range. Each batch is collected with the model it was submitted
with, even if the model variable has since changed. A provider switched off
after submission is still collected and advanced, since its results are
already paid for; a provider switched on after submission translates the
pending chunk directly.

If submitting to a second provider fails after the first succeeded, the first
batch is cancelled (best effort) and the run fails, so the next run submits
afresh. The workflow allows only one run at a time, so a manual run cannot
submit the same chunk twice alongside the scheduled one.

The manual run's `sync` input sets `USE_BATCH=0`: that run sends its requests
directly, at full price, and finishes the chunk the same day. A batch already
pending is still collected first.

## Reasoning ("thinking") settings

Translation does not benefit from extended reasoning, and reasoning tokens are
billed as output, so every request asks for the least reasoning its model
allows:

- `kimi` and `deepseek`: `thinking: {"type": "disabled"}`.
- `claude`: `thinking: {"type": "disabled"}` on most models (including
  `claude-sonnet-5`); `thinking: {"type": "between_tools"}` on
  `claude-sonnet-5-5`, which rejects `disabled`; and `effort: "low"` on
  `claude-opus-5-5` and the Fable/Mythos models, which cannot turn thinking
  off at all.

## Glossaries

Each provider-and-language combination keeps a running glossary of key Pali
terms at `translation/glossary-<provider>-<code>.md` — a Markdown table of
`Pali | rendering | notes` rows. Its purpose is twofold: a human-readable
guide to how doctrinally loaded terms are being translated, and a consistency
anchor so the same Pali word is rendered the same way from one run to the
next.

Each run injects the current glossary into that combination's translation
request. The model reuses the listed renderings and, after the verse
translation, reports any key terms it introduced that are not yet in the
glossary (doctrinal or technical terms only — never ordinary vocabulary or
proper names). Those new terms are appended to the file on a successful run;
entries are never rewritten or removed automatically. The files start empty
and are created on first use.

The glossaries are per provider so the bake-off stays honest: each provider's
word choices evolve from its own translation history, not from the other's.
(Kimi's and DeepSeek's glossaries were seeded with a copy of Claude's when each
provider was added, so established renderings start out shared; the lists
diverge only as each provider adds terms of its own.)

The directory is set by `GLOSSARY_DIR` (default `translation`).

## Section summaries

A chunk never crosses a section boundary, so whenever a chunk ends exactly at
one — an intra-chapter section break, a chapter/kanda `<div>` boundary, or the
end of a file — that chunk has completed a whole section. When that happens,
the job gathers the entire just-finished section's Pali (walking back to the
section's first verse, which may lie in an earlier run's chunk) and, for each
provider and target language, emails a single condensed **prose** summary:
verse boundaries are flattened and the text is abridged to roughly one tenth
its length (~90% reduction). The current glossary is supplied for term
consistency, but summaries do not themselves add glossary entries.

Summaries are part of the chunk's batch for batch providers. Each summary is
emailed (subject tagged with the provider) and also archived
to `translation/results/<provider>/summary-<code>-YYYY-MM-DD-runN.txt`
(sharing the run's date and run number). On a chunk that stops mid-section
(because it hit `CHUNK_SIZE`), no summary is produced.

### Backfilling a missed summary

If a section boundary went undetected, its summary was never sent and the job
has since read past it. Setting `SUMMARIZE_VERSE` to a verse number (or
`<file>.mul.xml:<verse>`) runs a summary-only pass over the whole section
containing that verse: it emails and archives the summaries exactly as a normal
run would — for every enabled provider, sent directly rather than batched —
but translates nothing, adds no
glossary terms, and leaves the saved position untouched. It is also exposed as
the `summarize_verse` input on the manual `workflow_dispatch` run.

## Undeliverable email

A failed send does **not** abort the run. The translation has already been made
and archived, so rolling back would only re-translate and re-send the same
chunk; instead the exact message is written to
`translation/state/pending-email/email-<UTC timestamp>-<NNN>.json` — subject,
body, the originating context (including the provider), an attempt count and
the last error. The recipient is deliberately not stored: it is a secret, and
resolving it from the environment at send time means a retry honours its
current value. Each spooled message is also logged as a GitHub Actions
warning, so the run shows the problem without going red.

At the start of every run, before any parsing or translation, the spool is
replayed oldest-first. A message that goes through is deleted; the first one
that fails again has its attempt count and error updated and the replay stops
there, leaving the rest queued in order for the next run rather than hammering
a service that is still down.

This applies to every outbound message — the per-provider per-language
readings, the section summaries, and the error reports themselves.

Two things make the spool durable, since each Actions run starts from a fresh
checkout and anything uncommitted is thrown away:

- the workflow's commit step adds all of `translation/state/`, not just the
  progress files, so spooled messages (and their later deletion) are
  committed;
- that step runs with `if: !cancelled()`, so a spooled message still gets
  committed on a run that failed for some other reason.

The spool directory is set by `EMAIL_SPOOL_DIR` (default
`translation/state/pending-email`) and is shared by all providers.

## File and directory layout

- `translation/scripts/daily_translate.py` — the entry point (stdlib-only
  Python, no pip install required). It just calls into the package below.
- `translation/scripts/tipitaka_translate/` — the job's implementation, split
  by concern:
  - `config.py` — provider registry (key/model env vars and defaults).
  - `prompts.py` — system prompts, target languages, shared prompt instructions.
  - `settings.py` — run configuration read from environment variables.
  - `source.py` — XML parsing, the verse model, and chunk selection.
  - `providers.py` — provider API clients, toggles, active-provider resolution,
    reasoning settings.
  - `batch.py` — batch submission, status, and results (`claude`, `kimi`).
  - `translation.py` — request assembly, direct calls, and usage lines.
  - `glossary.py` — glossary reading, new-term extraction, and appending.
  - `mail.py` — Resend delivery and the failed-email spool.
  - `archive.py` — result files under `translation/results/<provider>/`.
  - `state.py` — saved-position files, path derivation, lockstep checks, and
    the pending-batch record.
  - `diagnostics.py` — failure diagnosis and the error-report email.
  - `job.py` — orchestration: collect the pending batch, submit the next.
- `translation/state/providers.json` — one on/off boolean per provider; the
  normal way to enable or disable a provider (see "Providers" above).
- `translation/state/translation-progress-<provider>.json` — per-provider
  current position (file + next verse).
- `translation/state/pending-batch.json` — the batch submitted by the last
  run and not yet collected (see "Batching"). Absent when nothing is pending.
- `translation/state/pending-email/` — emails that failed to send, awaiting
  retry (see "Undeliverable email"). Empty in the normal case.
- `translation/results/<provider>/` — per-provider daily archive output
  (`pali-`, `eng-`, `zh-`, `full-` files per run; see
  `translation/results/README.md`).
- `translation/glossary-<provider>-<code>.md` — per-provider per-language
  key-term glossary (e.g. `glossary-claude-eng.md`, `glossary-kimi-zh.md`);
  see "Glossaries" above.
- `.github/workflows/daily-translation.yml` — the scheduled workflow.

## Configuration

Set these in the repo's Settings → Secrets and variables → Actions:

**Secrets**
- `ANTHROPIC_API_KEY` — used to call the Anthropic API for the `claude`
  provider.
- `KIMI_API_KEY` — used to call the Moonshot API for the `kimi` provider.
- `DEEPSEEK_API_KEY` — used to call the DeepSeek API for the `deepseek`
  provider.
- `RESEND_API_KEY` — used to send email via the Resend API.
- `EMAIL_TO` — destination address for the daily readings and any error reports.

**Variables**
- `EMAIL_FROM` — sender address (must be on a domain verified in Resend).
- `CHUNK_SIZE` (optional, default `20`) — verses per day.
- `ANTHROPIC_MODEL` (optional, default `claude-sonnet-5`).
- `KIMI_MODEL` (optional, default `kimi-k2.6`).
- `DEEPSEEK_MODEL` (optional, default `deepseek-flash`).

Provider enablement is not a repository variable: edit the booleans in
`translation/state/providers.json`. The `PROVIDERS` environment variable is
only set by the manual-run `providers` input, as an optional per-run subset of
the enabled providers (see "Providers").

**Repo setting**: Settings → Actions → General → Workflow permissions must be
set to "Read and write permissions" so the job's `GITHUB_TOKEN` can push its
own commits.

The workflow can also be run manually (`workflow_dispatch`) with an optional
`chunk_size` override, a `providers` input that narrows the enabled set for
that run, a `dry_run` toggle that skips the model calls, the emails, the
archive files, the glossary updates, and the state updates — it only logs the
selected chunk, for safe testing — and a `summarize_verse` input that backfills
a missed section summary (see "Backfilling a missed summary"), and a `sync`
toggle that sends that run's requests directly instead of batching them (see
"Batching"). With a batch pending, a dry run only reports it.

## Error handling

If translation, file-writing, or the state update fails at any point, the job
sends a separate error-report email instead (subject `Tipitaka job ERROR —
<context>`, containing the exception and a truncated traceback) and exits
non-zero. The context names the failing chunk and, once translation is under
way, the provider and language (e.g. `vin01m.mul.xml verses 12-31 (kimi
eng)`). If the error-report email itself can't be sent (e.g. broken email
secrets), the report is spooled for retry like any other message, and the run
still exits non-zero, so the failure is visible as a red run in the Actions
tab even in that fallback case.

A batch whose requests failed or expired is reported the same way (context
e.g. `vin01m.mul.xml verses 180-189 (collecting claude batch)`); the batch is
discarded first, so the next run resubmits the chunk. A batch that is simply
still running is not an error: the run logs it and exits successfully.

A send failure on its own is not one of these errors: it is spooled and
retried, and the run completes successfully.

## Known assumptions

- Dates and run numbering (`run1`, `run2`, ...) use UTC, matching the cron
  schedule.
- With all three providers enabled, each finished chunk emails one message per provider
  per target language (the verse-by-verse translation), plus, when a section
  completes, one condensed prose summary per provider per language — six
  reading emails and up to six summary emails per day. The Pali text and the
  combined record are archived on disk in `translation/results/<provider>/`
  but not emailed.
- File ordering is alphabetical across all `romn/*.mul.xml` files, not a
  curated canonical reading order — it starts at `vin01m.mul.xml` and wraps
  around after the last file.
