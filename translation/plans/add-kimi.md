# Plan: run Claude and Kimi translation providers in parallel

Run the daily job with both providers on the same chunk each day, keeping fully
separate state, glossaries, and result archives per provider, so the two can be
compared over a bake-off period (a week or more). Decisions already made with
the user:

- Emails stay **separate per provider**, subject tagged `[claude]` / `[kimi]`.
- Kimi's glossary is **seeded by copying Claude's** current glossary files;
  the two lists then grow independently.
- Both providers translate the **same chunk each run (lockstep)**; a chunk
  counts as done only when every configured provider succeeded.

## Design summary

The provider integration in `translation/scripts/daily_translate.py` is already
isolated in one function (`anthropic_message`, lines 353–379). Add a Kimi call
function beside it, put both behind a provider registry selected by a new
`PROVIDERS` env var (default "claude kimi"), and thread a provider key
through state/glossary/results paths. Everything else (chunking, email, spool,
glossary logic) is provider-agnostic and stays as-is.

The workflow stays a **single job that runs providers sequentially** (the
script loops over `PROVIDERS`). A matrix of two parallel jobs would race on the
final `git push` to the same branch; sequential costs only a few extra minutes
in a daily cron and produces one atomic commit.

## Step 1 — Confirm Kimi endpoint and model ID

Confirmed against Moonshot's official docs (`platform.moonshot.ai/docs/api/chat`):
endpoint `https://api.moonshot.ai/v1/chat/completions`, Bearer auth, current
model IDs `kimi-k3` / `kimi-k2.7-code` / `kimi-k2.6`. `kimi-k2.6` chosen as the
general-purpose default (k3 always thinks; k2.7-code is coding-specific).
`max_tokens` is deprecated in favor of `max_completion_tokens`; truncation
surfaces as `finish_reason: "length"`.

## Step 2 — `translation/scripts/daily_translate.py`

**Provider registry and dispatch** (new code near the top, replacing
`ANTHROPIC_URL`):

```python
PROVIDERS = {
    "claude": {
        "label": "Claude",
        "key_env": "ANTHROPIC_API_KEY",
        "model_env": "ANTHROPIC_MODEL",
        "model_default": "claude-sonnet-5",
    },
    "kimi": {
        "label": "Kimi",
        "key_env": "KIMI_API_KEY",
        "model_env": "KIMI_MODEL",
        "model_default": "kimi-k2.6",
    },
}
```

- Keep `anthropic_message()` as the claude call (unchanged).
- Add `kimi_message(api_key, model, system, user_msg, truncation_hint)`:
  `POST https://api.moonshot.ai/v1/chat/completions`, headers
  `content-type: application/json` + `Authorization: Bearer <key>`, body
  `{"model", "max_completion_tokens": 16000, "messages": [{"role": "system", ...}, {"role": "user", ...}]}`, timeout 180s. Parse `choices[0]["message"]
  ["content"]` (accept both a plain string and a list of `{type: "text"}`
  parts, defensively). Truncation signal is `finish_reason == "length"` (vs
  Anthropic's `stop_reason == "max_tokens"`).
- Add `chat_message(provider, ...)` that dispatches on the provider key.
- Rename `call_anthropic` → `call_model(provider_cfg, ...)` and have it call
  `chat_message`; `summarize_section` gains a provider parameter. All
  prompt-building code is provider-agnostic and does not change.

**Per-provider paths** — new helpers, env overrides stay the same for the
base directories:

- State: `translation/state/translation-progress-<provider>.json`
  (default `STATE_FILE` becomes a base name; per-provider files derive from it).
- Glossary: `translation/glossary-<provider>-<code>.md` via
  `glossary_path(glossary_dir, provider, code)`.
- Results: `translation/results/<provider>/` via `translation_dir(provider)`.
  `next_run_number` / `write_translation_files` / `write_summary_files` operate
  on the per-provider directory; call once per provider.

**Lockstep main loop** (`main`, lines 752–877, and `emit_section_summaries`,
lines 662–696):

1. Parse `PROVIDERS` (whitespace-separated) from env; reject unknown names.
2. Load all providers' state files; if more than one provider is configured
   and the states differ, raise a clear error (states are kept in lockstep;
   divergence means someone ran a subset — tell the operator to finish the
   catch-up single-provider run or fix the JSON).
3. Select the chunk from the (equal) state.
4. Translate **all providers × all languages first**, collecting results —
   no emails yet. This removes the current duplicate-email-on-partial-failure
   wrinkle (today a zh failure re-sends eng the next day).
5. Email each translation separately, subject prefixed with the provider tag:
   `Tipitaka reading [kimi]: …` / `Tipitaka reading [claude]: …`; summaries
   likewise (`Tipitaka section summary [kimi] (Traditional Chinese): …`).
6. Section summaries: loop providers × languages through `summarize_section`.
7. Archive per provider (each provider's dir gets its own `pali-`, `eng-`,
   `zh-`, `full-`, `summary-*` set, same date + run counter), append new
   glossary terms per provider, then write every provider's state file.
8. Failure semantics unchanged in spirit: anything in steps 4–7 failing →
   error-report email (subject `Tipitaka job ERROR — <context>`) whose context
   names the provider (e.g. `vin01m.mul.xml verses 12-31 (kimi)`), nothing
   archived/advanced, non-zero exit. Send failures still spool (shared spool;
   each spool record's `context` field gains the provider name).
9. `run_backfill_summary` honors `PROVIDERS` too (summarize/emails/archive
   per configured provider; state untouched).
10. Update the module docstring (line 6) to describe the provider dimension.

## Step 3 — Migrate existing artifacts (git mv, history preserved)

- `translation/state/translation-progress.json` →
  `translation/state/translation-progress-claude.json`, and a copy seeds
  `translation/state/translation-progress-kimi.json` (positions start equal).
- `translation/results/*.txt` (all existing archive files) →
  `translation/results/claude/`; `translation/results/kimi/` is created on
  first use.
- `translation/glossary-eng.md` → `translation/glossary-claude-eng.md`;
  `translation/glossary-zh.md` → `translation/glossary-claude-zh.md`.
- Seed Kimi: copy both files to `translation/glossary-kimi-eng.md` /
  `translation/glossary-kimi-zh.md`, byte-identical. Because the glossary
  reader keys on the first table cell, the kimi copies function immediately;
  the header line inside each file still says "English glossary" — acceptable,
  since both files are per-language anyway (adjust only if the header is
  provider-specific; it is not).

## Step 4 — `.github/workflows/daily-translation.yml`

- Add to env: `PROVIDERS: ${{ inputs.providers || vars.PROVIDERS || 'claude kimi' }}`,
  `KIMI_API_KEY: ${{ secrets.KIMI_API_KEY }}`,
  `KIMI_MODEL: ${{ vars.KIMI_MODEL || 'kimi-k2.6' }}`.
- Add a `providers` string input to `workflow_dispatch`.
- Commit step: existing `git add translation/state/ translation/results/
  translation/glossary-*.md` already covers the new layout (glossary files
  keep the `glossary-` prefix; results subdirs are under `translation/results/`)
  — verify the glob matches `glossary-kimi-eng.md` (it does) and change nothing.

## Step 5 — Docs

- `translation/documentation/daily-translation-job.md`:
  - Flow: translate per provider, tagged separate emails per provider,
    per-provider archives/glossaries/states, lockstep atomicity (a chunk
    advances only when all configured providers succeeded).
  - New "Providers" section: registry, per-provider key/model env vars, the
    lockstep rule, the subset escape hatch, and the state-divergence guard.
  - "Glossaries": per-provider files, kimi seeded from claude's.
  - "File and directory layout": new paths.
  - "Configuration": add `KIMI_API_KEY` secret, `KIMI_MODEL` and `PROVIDERS`
    variables.
  - "Error handling" and "Known assumptions": provider-tagged emails now
    number 2× per language when both providers run; error contexts name the
    provider.
- `translation/results/README.md`: archive now lives in per-provider
  subdirectories (`claude/`, `kimi/`).
- `CLAUDE.md` is a doctrinal glossary — untouched.

## Step 6 — Verification (all passed)

1. `python3 -m py_compile translation/scripts/daily_translate.py`.
2. `DRY_RUN=1` run with `PROVIDERS='claude kimi'`: per-provider state loading,
   the divergence guard, chunk selection from migrated claude state, and
   provider parsing all exercised.
3. Mocked `kimi_message` via a throwaway monkeypatched `urllib.request.urlopen`:
   request-body shape (system-as-message, bearer auth, `max_completion_tokens`),
   response parsing (plain string and list-of-parts content), the
   `finish_reason == "length"` truncation path, and `chat_message` dispatch.
4. Provider-list parsing edge cases: unknown name, duplicate, empty, missing
   API key.
5. Divergence guard: disagreeing states → clear RuntimeError, exit 1.
6. Backfill dry run with both providers; single-provider (`PROVIDERS=kimi`)
   dry run.
7. Workflow YAML parses.

Not verifiable without real credentials: a live end-to-end Kimi round-trip.
The first real dual-provider run requires the `KIMI_API_KEY` secret in repo
Settings and is best done as a watched manual `workflow_dispatch`, comparing
the two tagged emails and the two archive directories against each other.

## Notes / trade-offs

- Cost: doubles daily API calls (~4 → ~8), trivial at this scale.
- If one provider has an outage stretch, set `PROVIDERS` to the healthy one;
  states will diverge by design, and the guard in step 2 prevents a silent
  mismatched side-by-side comparison when both are re-enabled.
- No new dependencies: both call paths stay stdlib `urllib`.
