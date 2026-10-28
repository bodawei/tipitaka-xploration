#!/usr/bin/env python3
"""
Daily Tipitaka reading job.

Reads the next chunk of verses from the current position in a rotating list of
romn/*.mul.xml files and translates it into each configured target language
with every provider enabled in the provider state file (claude, kimi, and
deepseek by default), so the providers can be compared side by side. The
providers translate the same chunk without consulting each other and keep
fully separate progress state, glossaries, and result archives. Each
translation is emailed via Resend and the on-disk progress state is advanced.
If every provider is disabled, the job exits without doing any work.

This file is only the entry point. The implementation lives in the
`tipitaka_translate` package beside it; see that package's `__init__` for a map
of its modules.

A "verse" is a <p rend="bodytext" n="N"> element plus any immediately
following unnumbered <p rend="bodytext"> continuation paragraphs. A chunk
never crosses a chapter/kanda boundary (i.e. never spans two different
immediate parent <div> elements), even if that means fewer verses than
CHUNK_SIZE.
"""
from tipitaka_translate.job import main

if __name__ == "__main__":
    main()
