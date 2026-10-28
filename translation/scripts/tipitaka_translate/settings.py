"""Run configuration read from the environment."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    """Paths and flags for one job run.

    Defaults match the repository layout, so the job works unchanged when run
    from the repo root with no environment set.
    """

    source_dir: str
    state_base: str
    providers_file: str
    translation_dir: str
    glossary_dir: str
    spool_dir: str
    chunk_size: int
    dry_run: bool
    summarize_verse: str

    @classmethod
    def from_environment(cls):
        return cls(
            source_dir=os.environ.get("SOURCE_DIR", "romn"),
            state_base=os.environ.get(
                "STATE_FILE", "translation/state/translation-progress.json"
            ),
            providers_file=os.environ.get(
                "PROVIDERS_FILE", "translation/state/providers.json"
            ),
            translation_dir=os.environ.get("TRANSLATION_DIR", "translation/results"),
            glossary_dir=os.environ.get("GLOSSARY_DIR", "translation"),
            spool_dir=os.environ.get(
                "EMAIL_SPOOL_DIR", "translation/state/pending-email"
            ),
            chunk_size=int(os.environ.get("CHUNK_SIZE", "15")),
            dry_run=os.environ.get("DRY_RUN") == "1",
            summarize_verse=os.environ.get("SUMMARIZE_VERSE", "").strip(),
        )
