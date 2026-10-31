"""Orchestration for the daily translation job.

Providers with a batch API (claude, kimi) are sent each chunk as an
asynchronous, discounted batch, so a chunk takes two runs:

1. **Collect.** If an earlier run left a pending batch, check it. Still
   running: stop and try again next run. Finished: fetch the results, run
   the direct-only providers (deepseek) on the same chunk, then email,
   archive, add glossary terms, and advance every provider's saved position
   together.
2. **Submit.** Select the next chunk and submit it to each batch provider,
   recording the batch ids in the pending-batch file for the next run.

With no batch provider enabled (or USE_BATCH=0), a chunk is translated and
finished in the same run, as before batching existed.

Finishing is atomic: a failure before the state files are written means
nothing is archived, no glossary terms are added, and the same chunk is
retried on the next run (a pending batch is kept and re-read). A batch whose
requests failed or expired is discarded and its chunk resubmitted next run.
Failed sends are the one exception — they are spooled for retry instead of
rolling the run back.
"""
import os
import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone

from . import archive, batch, diagnostics, glossary, mail, providers, source, state, translation
from .prompts import LANGUAGES
from .settings import Settings


def _heading_str(heading, fallback):
    return " — ".join(heading) if heading else fallback


def _label_suffix(lang):
    return "" if lang["code"] == "eng" else f" ({lang['label']})"


@dataclass
class Chunk:
    """One selected chunk, plus everything needed to reproduce the selection."""

    position: dict
    chunk_size: int
    filename: str
    verses: list
    heading: list
    new_state: dict
    section_verses: list

    @property
    def verse_range(self):
        return source.verse_label(self.verses[0]["n"], self.verses[-1]["n_last"])

    @property
    def heading_str(self):
        return _heading_str(self.heading, self.filename)

    @property
    def header(self):
        return f"{self.heading_str}\n{self.filename}, verses {self.verse_range}\n"


def select(files, source_dir, position, chunk_size):
    filename, verses, heading, new_state, section_verses = source.select_chunk(
        files, source_dir, position, chunk_size
    )
    return Chunk(position, chunk_size, filename, verses, heading, new_state, section_verses)


def section_header(filename, section_verses):
    sec_range = source.verse_label(section_verses[0]["n"], section_verses[-1]["n_last"])
    sec_heading_str = _heading_str(section_verses[0]["heading"], filename)
    header = (
        f"{sec_heading_str}\n{filename}, section verses {sec_range} "
        f"(condensed prose summary)\n"
    )
    return sec_range, sec_heading_str, header


def summary_requests(name, filename, section_verses, glossaries):
    return {
        f"summary-{lang['code']}": translation.summary_request(
            lang["summary_system"], filename, section_verses[0]["heading"],
            section_verses, glossaries[(name, lang["code"])][0],
        )
        for lang in LANGUAGES
    }


def chunk_requests(name, chunk, glossaries):
    """Every request one provider makes for a chunk, keyed by custom_id:
    one translation per language, plus one summary per language when the
    chunk completes a section."""
    requests = {
        f"translate-{lang['code']}": translation.translation_request(
            lang["system"], chunk.filename, chunk.heading, chunk.verses,
            glossaries[(name, lang["code"])][0],
        )
        for lang in LANGUAGES
    }
    if chunk.section_verses:
        requests.update(summary_requests(name, chunk.filename, chunk.section_verses, glossaries))
    return requests


def run_direct(provider, requests, set_context, label):
    """Send a provider's requests one by one; returns {custom_id: (text, usage_header)}."""
    outputs = {}
    for cid, request in requests.items():
        set_context(f"{label} ({provider['name']} {cid})")
        print(f"Requesting {cid} ({provider['name']})...")
        text, usage = translation.run(provider, request)
        outputs[cid] = (
            text,
            translation.usage_header(provider["name"], provider["model"], usage, "direct API"),
        )
    return outputs


def send_summaries(name, filename, section_verses, outputs, account, spool_dir):
    """Email one provider's section summaries; returns (header, texts, usage)."""
    sec_range, sec_heading_str, sec_header = section_header(filename, section_verses)
    texts, usage = {}, {}
    for lang in LANGUAGES:
        code = lang["code"]
        texts[code], usage[code] = outputs[f"summary-{code}"]
        mail.send_email_or_spool(
            account,
            spool_dir,
            f"Tipitaka section summary [{name}]{_label_suffix(lang)}: "
            f"{filename} verses {sec_range} — {sec_heading_str}",
            f"{usage[code]}\n\n{sec_header}\n{texts[code]}",
            f"{filename} section summary {sec_range} [{name}] ({lang['label']})",
        )
    return sec_header, texts, usage


def finish_chunk(chunk, outputs, settings, account):
    """Email, archive, extend glossaries, and advance state for every provider
    in `outputs` ({provider: {custom_id: (text, usage_header)}}).

    Every result is already in hand, so nothing here calls a model."""
    names = list(outputs)
    glossaries = glossary.load_glossaries([{"name": n} for n in names], settings.glossary_dir)
    date_str = datetime.now(timezone.utc).date().isoformat()

    translations, usage, new_terms = {}, {}, {}
    for name in names:
        translations[name], usage[name], new_terms[name] = {}, {}, {}
        for lang in LANGUAGES:
            code = lang["code"]
            text, usage[name][code] = outputs[name][f"translate-{code}"]
            translations[name][code], new_terms[name][code] = (
                glossary.split_translation_and_terms(text)
            )
            mail.send_email_or_spool(
                account,
                settings.spool_dir,
                f"Tipitaka reading [{name}]{_label_suffix(lang)}: "
                f"{chunk.filename} verses {chunk.verse_range} — {chunk.heading_str}",
                f"{usage[name][code]}\n\n{chunk.header}\n{translations[name][code]}",
                f"{chunk.filename} verses {chunk.verse_range} [{name}] ({lang['label']})",
            )

    if chunk.section_verses:
        print(f"Section complete ({chunk.filename}); emailing condensed summaries")

    pali_text = source.format_pali(chunk.verses)
    for name in names:
        out_dir = os.path.join(settings.translation_dir, name)
        run_n = archive.next_run_number(out_dir, date_str)
        archive.write_translation_files(
            out_dir, date_str, run_n, chunk.header, translations[name], usage[name], pali_text
        )
        if chunk.section_verses:
            sec_header, texts, sec_usage = send_summaries(
                name, chunk.filename, chunk.section_verses, outputs[name],
                account, settings.spool_dir,
            )
            archive.write_summary_files(out_dir, date_str, run_n, sec_header, texts, sec_usage)

    for name in names:
        for lang in LANGUAGES:
            code = lang["code"]
            glossary.append_glossary(
                glossary.glossary_path(settings.glossary_dir, name, code),
                lang["label"],
                new_terms[name][code], glossaries[(name, code)][1],
            )

    for name in names:
        state.save_state(settings.state_base, name, chunk.new_state)
    print(f"Finished {chunk.filename} verses {chunk.verse_range}; state updated: {chunk.new_state}")


def collect_pending(pending, active, files, settings, account, set_context):
    """Try to finish the chunk an earlier run submitted.

    Returns True if the chunk was finished, False if a batch is still running.
    Raises if the batch failed (after discarding it, so the chunk is
    resubmitted next run) or if anything else goes wrong (keeping it, so
    the next run retries the collection).
    """
    chunk = select(files, settings.source_dir, pending["position"], pending["chunk_size"])
    if (chunk.filename, chunk.verse_range) != (pending["file"], pending["verse_range"]):
        raise RuntimeError(
            f"Pending batch was for {pending['file']} verses {pending['verse_range']}, "
            f"but re-selecting from its position gives {chunk.filename} verses "
            f"{chunk.verse_range}. Has the source changed? Inspect or delete "
            f"{settings.pending_batch_file}."
        )
    label = f"{chunk.filename} verses {chunk.verse_range}"
    batched = {
        name: providers.provider_config(name, entry["model"])
        for name, entry in pending["batches"].items()
    }

    for name, provider in batched.items():
        set_context(f"{label} (checking {name} batch)")
        finished, status_text = batch.status(provider, pending["batches"][name]["id"])
        if not finished:
            print(
                f"{name} batch for {label} (submitted {pending['submitted_at']}) "
                f"is still {status_text}; will check again next run."
            )
            return False

    hints = {
        cid: request["hint"]
        for cid, request in chunk_requests(
            "_", chunk, {("_", lang["code"]): ("", set()) for lang in LANGUAGES}
        ).items()
    }
    outputs, errors = {}, []
    for name, provider in batched.items():
        set_context(f"{label} (collecting {name} batch)")
        succeeded, provider_errors = batch.results(
            provider, pending["batches"][name]["id"], hints
        )
        errors += [f"[{name}] {e}" for e in provider_errors]
        via = f"batch API, {batch.DISCOUNT[name]}"
        outputs[name] = {
            cid: (text, translation.usage_header(name, provider["model"], usage, via))
            for cid, (text, usage) in succeeded.items()
        }
    if errors:
        state.clear_pending(settings.pending_batch_file)
        raise RuntimeError(
            f"Batch requests for {label} failed; the batch is discarded and the "
            f"chunk will be resubmitted on the next run:\n" + "\n".join(errors)
        )

    # Providers enabled now but not part of the batch (DeepSeek, which has no
    # batch API, or a batch provider enabled since submission) translate the
    # same chunk directly, so every provider's state advances together.
    direct = [p for p in active if p["name"] not in batched]
    if direct:
        glossaries = glossary.load_glossaries(direct, settings.glossary_dir)
        for p in direct:
            outputs[p["name"]] = run_direct(
                p, chunk_requests(p["name"], chunk, glossaries), set_context, label
            )

    set_context(label)
    finish_chunk(chunk, outputs, settings, account)
    state.clear_pending(settings.pending_batch_file)
    return True


def submit_chunk(chunk, batch_providers, settings, set_context):
    """Submit a chunk to every batch provider and record the pending batch."""
    label = f"{chunk.filename} verses {chunk.verse_range}"
    glossaries = glossary.load_glossaries(batch_providers, settings.glossary_dir)
    record = {
        "submitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "position": chunk.position,
        "chunk_size": chunk.chunk_size,
        "file": chunk.filename,
        "verse_range": chunk.verse_range,
        "batches": {},
    }
    submitted = []
    try:
        for p in batch_providers:
            set_context(f"{label} (submitting {p['name']} batch)")
            batch_id = batch.submit(p, chunk_requests(p["name"], chunk, glossaries))
            submitted.append((p, batch_id))
            record["batches"][p["name"]] = {"id": batch_id, "model": p["model"]}
    except Exception:
        for p, batch_id in submitted:
            batch.cancel(p, batch_id)
        raise
    state.save_pending(settings.pending_batch_file, record)
    print(
        f"Submitted {label} ({chunk.heading_str}) as batch(es): "
        + ", ".join(f"{n} {e['id']}" for n, e in record["batches"].items())
    )


def run_backfill_summary(active, saved_state, settings, account, set_context):
    """Re-issue the section summary for the section containing a given verse.

    Set SUMMARIZE_VERSE to "<verse>" (in the file the state currently points
    at) or "<file>:<verse>". This emails and archives the summaries exactly as
    a normal run would (sent directly, not batched), but translates nothing,
    advances no state, and adds no glossary terms — it exists to recover a
    summary that an earlier run missed because it failed to detect the
    section boundary.
    """
    filename, _, verse_text = settings.summarize_verse.rpartition(":")
    filename = filename or saved_state["file"]
    verse_n = int(verse_text)

    verses = source.parse_verses(os.path.join(settings.source_dir, filename))
    section_verses = source.find_section(verses, verse_n)
    print(
        f"Backfilling summary for {filename} section containing verse {verse_n}: "
        f"verses {source.verse_label(section_verses[0]['n'], section_verses[-1]['n_last'])}"
    )

    if settings.dry_run:
        print("[DRY RUN — summary skipped]")
        return

    glossaries = glossary.load_glossaries(active, settings.glossary_dir)
    date_str = datetime.now(timezone.utc).date().isoformat()
    for p in active:
        outputs = run_direct(
            p, summary_requests(p["name"], filename, section_verses, glossaries),
            set_context, f"{filename} summary backfill",
        )
        sec_header, texts, usage = send_summaries(
            p["name"], filename, section_verses, outputs, account, settings.spool_dir
        )
        out_dir = os.path.join(settings.translation_dir, p["name"])
        run_n = archive.next_run_number(out_dir, date_str)
        archive.write_summary_files(out_dir, date_str, run_n, sec_header, texts, usage)
    print("Backfill complete; state left unchanged")


def main():
    settings = Settings.from_environment()
    account = None

    context = "startup"
    try:
        active = providers.load_active_providers(
            providers.load_provider_toggles(settings.providers_file)
        )
        pending = state.load_pending(settings.pending_batch_file)
        if not active and not pending:
            # Nothing is switched on, so there is no translation, email, or
            # state update to do. Exit successfully without touching anything.
            print(
                f"All providers are disabled in {settings.providers_file}; "
                "nothing to do."
            )
            return

        files = sorted(
            f for f in os.listdir(settings.source_dir) if f.endswith(".mul.xml")
        )
        if not files:
            raise RuntimeError(f"No *.mul.xml files found in {settings.source_dir}")

        def set_context(value):
            nonlocal context
            context = value

        # A provider with an uncollected batch is tracked (and finished) even
        # if it has since been switched off: its results are already paid for.
        tracked = list(active)
        for name in (pending or {}).get("batches", {}):
            if name not in {p["name"] for p in tracked}:
                tracked.append({"name": name})
        states = {}
        for p in tracked:
            set_context(f"loading saved position ({p['name']})")
            states[p["name"]] = state.load_state(settings.state_base, p["name"])
        saved_state = state.require_lockstep(states, tracked, settings.state_base)
        set_context(
            f"file={saved_state.get('file')}, next_verse={saved_state.get('next_verse')}"
        )

        # Retry anything an earlier run could not send, before doing any work
        # of our own — so a transient mail outage self-heals on the next run.
        if not settings.dry_run:
            set_context("retrying spooled emails")
            account = mail.EmailAccount.from_environment()
            mail.flush_pending_emails(account, settings.spool_dir)
            set_context(
                f"file={saved_state.get('file')}, "
                f"next_verse={saved_state.get('next_verse')}"
            )

        if settings.summarize_verse:
            run_backfill_summary(active, saved_state, settings, account, set_context)
            return

        if pending:
            if settings.dry_run:
                print(
                    f"[DRY RUN] Pending batch for {pending['file']} verses "
                    f"{pending['verse_range']} (submitted {pending['submitted_at']}): "
                    + ", ".join(f"{n} {e['id']}" for n, e in pending["batches"].items())
                    + " — not checked, and no new chunk selected."
                )
                return
            if pending["position"] != saved_state:
                raise RuntimeError(
                    f"Pending batch was selected from {pending['position']}, but the "
                    f"saved position is now {saved_state}. Inspect or delete "
                    f"{settings.pending_batch_file}."
                )
            if not collect_pending(pending, active, files, settings, account, set_context):
                return
            saved_state = state.load_state(settings.state_base, tracked[0]["name"])

        if not active:
            print("All providers are now disabled; nothing new to submit.")
            return

        chunk = select(files, settings.source_dir, saved_state, settings.chunk_size)
        label = f"{chunk.filename} verses {chunk.verse_range}"
        set_context(label)
        print(f"Selected {label} ({chunk.heading_str}), {len(chunk.verses)} verse(s)")

        if settings.dry_run:
            print(f"{chunk.header}\n[DRY RUN — translation skipped]")
            return

        batch_providers = [
            p for p in active if settings.use_batch and p["name"] in batch.BATCH_CAPABLE
        ]
        if batch_providers:
            submit_chunk(chunk, batch_providers, settings, set_context)
            return

        # No batch provider: translate and finish the chunk in this run.
        # Every request completes before any email goes out, so a failure
        # anywhere means the whole chunk simply retries on the next run.
        glossaries = glossary.load_glossaries(active, settings.glossary_dir)
        outputs = {
            p["name"]: run_direct(
                p, chunk_requests(p["name"], chunk, glossaries), set_context, label
            )
            for p in active
        }
        set_context(label)
        finish_chunk(chunk, outputs, settings, account)

    except Exception as exc:
        print(f"ERROR during {context}: {exc}", file=sys.stderr)
        traceback.print_exc()
        if not settings.dry_run:
            try:
                if account is None:
                    account = mail.EmailAccount.from_environment()
                diagnostics.send_error_report(
                    account, settings.spool_dir, context, exc
                )
            except Exception as report_exc:
                print(
                    f"Additionally failed to send error report: {report_exc}",
                    file=sys.stderr,
                )
        sys.exit(1)
