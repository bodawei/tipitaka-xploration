"""Implementation package for the daily Tipitaka translation job.

The `daily_translate.py` script in the parent directory is the entry point; the
job itself is split across these modules:

- config       provider registry (API key/model env vars, defaults)
- prompts      system prompts, target languages, shared prompt instructions
- settings     run configuration read from environment variables
- source       XML parsing, verse model, and chunk selection
- providers    provider API clients, toggle loading, active-provider resolution
- batch        asynchronous batch submit/status/results (claude, kimi)
- translation  request assembly, direct calls, and usage lines
- glossary     glossary reading, new-term extraction, and appending
- mail         Resend delivery and the failed-email spool
- archive      writing result files under translation/results/<provider>/
- state        per-provider saved-position files, lockstep checks, and the
               pending-batch record
- diagnostics  failure diagnosis and the error-report email
- job          orchestration (collect yesterday's batch, submit today's)
"""
