"""Orchestration for the daily translation job: one day's chunk, end to end.

Flow, per run: resolve providers and their (lockstep) saved position; replay
any spooled email; select the next chunk of Pali; translate it with every
provider and language; email and archive the results; add new glossary terms;
advance the saved position. A chunk that completes a section also triggers a
condensed prose summary of the whole section.

Steps after selection are atomic: a failure before the state files are written
means nothing is archived, no glossary terms are added, and the same chunk is
retried on the next run. Failed sends are the one exception — they are spooled
for retry instead of rolling the run back.
"""
import os
import sys
import traceback
from datetime import datetime, timezone

from . import archive, diagnostics, glossary, mail, providers, source, state, translation
from .prompts import LANGUAGES
from .settings import Settings


def _heading_str(heading, fallback):
    return " — ".join(heading) if heading else fallback


def emit_section_summaries(active, filename, section_verses, glossaries, account, spool_dir):
    """Summarise a whole section with every provider into every language, email
    each summary separately, and return (header, {provider: {code: summary}})
    for archiving."""
    sec_range = source.verse_label(section_verses[0]["n"], section_verses[-1]["n_last"])
    sec_heading = section_verses[0]["heading"]
    sec_heading_str = _heading_str(sec_heading, filename)
    sec_header = (
        f"{sec_heading_str}\n{filename}, section verses {sec_range} "
        f"(condensed prose summary)\n"
    )

    print(f"Section complete ({filename} verses {sec_range}); emailing condensed summaries")
    summaries = {}
    for p in active:
        name = p["name"]
        summaries[name] = {}
        for lang in LANGUAGES:
            code = lang["code"]
            print(f"Summarising section into {lang['label']} ({name})...")
            summaries[name][code] = translation.summarize_section(
                name, p["key"], p["model"], lang["summary_system"],
                filename, sec_heading, section_verses,
                glossary=glossaries[(name, code)][0],
            )
            label_suffix = "" if code == "eng" else f" ({lang['label']})"
            subject = (
                f"Tipitaka section summary [{name}]{label_suffix}: "
                f"{filename} verses {sec_range} — {sec_heading_str}"
            )
            mail.send_email_or_spool(
                account,
                spool_dir,
                subject,
                f"{sec_header}\n{summaries[name][code]}",
                f"{filename} section summary {sec_range} [{name}] ({lang['label']})",
            )
    return sec_header, summaries


def run_backfill_summary(active, saved_state, settings, account):
    """Re-issue the section summary for the section containing a given verse.

    Set SUMMARIZE_VERSE to "<verse>" (in the file the state currently points
    at) or "<file>:<verse>". This emails and archives the summaries exactly as
    a normal run would, but translates nothing, advances no state, and adds no
    glossary terms — it exists to recover a summary that an earlier run missed
    because it failed to detect the section boundary.
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
    sec_header, summaries = emit_section_summaries(
        active, filename, section_verses, glossaries, account, settings.spool_dir
    )

    date_str = datetime.now(timezone.utc).date().isoformat()
    for p in active:
        out_dir = os.path.join(settings.translation_dir, p["name"])
        run_n = archive.next_run_number(out_dir, date_str)
        archive.write_summary_files(
            out_dir, date_str, run_n, sec_header, summaries[p["name"]]
        )
    print("Backfill complete; state left unchanged")


def _load_states(active, state_base, set_context):
    states = {}
    for p in active:
        set_context(f"loading saved position ({p['name']})")
        states[p["name"]] = state.load_state(state_base, p["name"])
    return states


def _translate_chunk(active, filename, verse_range, heading, chunk, glossaries, set_context):
    """Translate one chunk with every provider and language.

    Returns ({provider: {code: translation}}, {provider: {code: new_terms}}).
    """
    translations = {}
    new_terms = {}
    for p in active:
        name = p["name"]
        translations[name] = {}
        new_terms[name] = {}
        for lang in LANGUAGES:
            code = lang["code"]
            set_context(f"{filename} verses {verse_range} ({name} {code})")
            print(f"Translating to {lang['label']} ({name})...")
            response = translation.call_model(
                name, p["key"], p["model"], lang["system"], filename,
                heading, chunk, glossary=glossaries[(name, code)][0],
            )
            translations[name][code], new_terms[name][code] = (
                glossary.split_translation_and_terms(response)
            )
    return translations, new_terms


def main():
    settings = Settings.from_environment()
    account = None

    context = "startup"
    try:
        active = providers.load_active_providers(
            providers.load_provider_toggles(settings.providers_file)
        )
        if not active:
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

        states = _load_states(active, settings.state_base, set_context)
        saved_state = state.require_lockstep(states, active, settings.state_base)
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
            run_backfill_summary(active, saved_state, settings, account)
            return

        filename, chunk, heading, new_state, section_verses = source.select_chunk(
            files, settings.source_dir, saved_state, settings.chunk_size
        )
        verse_range = source.verse_label(chunk[0]["n"], chunk[-1]["n_last"])
        heading_str = _heading_str(heading, filename)
        set_context(f"{filename} verses {verse_range}")

        print(
            f"Translating {filename} verses {verse_range} ({heading_str}), "
            f"{len(chunk)} verse(s)"
        )

        header = f"{heading_str}\n{filename}, verses {verse_range}\n"
        pali_text = source.format_pali(chunk)

        if settings.dry_run:
            translation_text = "[DRY RUN — translation skipped]"
            print(f"{header}\n{translation_text}")
            return

        glossaries = glossary.load_glossaries(active, settings.glossary_dir)

        # Translate with every provider and language before any email goes
        # out: a failure anywhere below means no emails, no archive, no
        # glossary additions, and no state advance — the whole chunk simply
        # retries on the next run.
        translations, new_terms = _translate_chunk(
            active, filename, verse_range, heading, chunk, glossaries, set_context
        )
        set_context(f"{filename} verses {verse_range}")

        # Every translation succeeded — now send the emails.
        for p in active:
            name = p["name"]
            for lang in LANGUAGES:
                code = lang["code"]
                label_suffix = "" if code == "eng" else f" ({lang['label']})"
                subject = (
                    f"Tipitaka reading [{name}]{label_suffix}: "
                    f"{filename} verses {verse_range} — {heading_str}"
                )
                mail.send_email_or_spool(
                    account,
                    settings.spool_dir,
                    subject,
                    f"{header}\n{translations[name][code]}",
                    f"{filename} verses {verse_range} [{name}] ({lang['label']})",
                )

        summaries = {}
        sec_header = None
        if section_verses:
            set_context(f"{filename} verses {verse_range} (section summaries)")
            sec_header, summaries = emit_section_summaries(
                active, filename, section_verses, glossaries, account, settings.spool_dir
            )
            set_context(f"{filename} verses {verse_range}")

        date_str = datetime.now(timezone.utc).date().isoformat()
        for p in active:
            out_dir = os.path.join(settings.translation_dir, p["name"])
            run_n = archive.next_run_number(out_dir, date_str)
            archive.write_translation_files(
                out_dir, date_str, run_n, header, translations[p["name"]], pali_text
            )
            if summaries:
                archive.write_summary_files(
                    out_dir, date_str, run_n, sec_header, summaries[p["name"]]
                )

        for p in active:
            for lang in LANGUAGES:
                code = lang["code"]
                glossary.append_glossary(
                    glossary.glossary_path(settings.glossary_dir, p["name"], code),
                    lang["label"],
                    new_terms[p["name"]][code], glossaries[(p["name"], code)][1],
                )

        for p in active:
            state.save_state(settings.state_base, p["name"], new_state)

        print(f"State updated: {new_state}")

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
