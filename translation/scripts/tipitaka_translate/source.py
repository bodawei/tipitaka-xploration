"""Parsing of the CSCD XML source into verses, and chunk selection.

The markup model, in brief: a "verse" is a <p rend="bodytext" n="N"> element
plus any immediately following unnumbered <p rend="bodytext"> continuation
paragraphs. A chunk never crosses a chapter/kanda boundary (a different
immediate parent <div>) or an intra-chapter section boundary, even if that
means fewer verses than the configured chunk size.
"""
import os
import re
import xml.etree.ElementTree as ET

WHITESPACE_RE = re.compile(r"\s+")


def normalize(text):
    return WHITESPACE_RE.sub(" ", text).strip()


def clean_text(elem):
    parts = [elem.text or ""]
    for child in elem:
        skip = child.tag == "note" or (
            child.tag == "hi" and child.get("rend") in ("paranum", "dot")
        )
        if not skip:
            parts.append(clean_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


# Paragraph styles that title a new division of text. The CSCD markup uses a
# whole family of these at different ranks (nikaya > book > chapter > title >
# subhead > subsubhead) and picks whichever suits the level being opened, so a
# section head must be recognised by any of them, not by `subhead` alone — e.g.
# "3. Tatiyapārājikaṃ" opens its section with `title`. A <head> inside a <div>
# is already covered by the parent-<div> check in select_chunk.
SECTION_HEAD_RENDS = frozenset(
    ("nikaya", "book", "chapter", "title", "subhead", "subsubhead")
)

# Closing formulae for a just-finished section, carried by a centred paragraph
# or a <trailer>. Two are in use and they are interchangeable: "... niṭṭhito /
# niṭṭhitaṃ" ("is finished", e.g. "Sudinnabhāṇavāro niṭṭhito.") and "... samatto
# / samattaṃ" ("is completed", e.g. "Dutiyapārājikaṃ samattaṃ.").
SECTION_END_RE = re.compile("niṭṭhit|samatt")


def starts_new_section(elem):
    """Whether `elem` is an intra-chapter section boundary marker.

    Sections within a single <div> are delimited by a heading for the new
    section (see SECTION_HEAD_RENDS) and/or a closing trailer for the previous
    one (see SECTION_END_RE). A chunk must not cross such a boundary, even
    though it does not coincide with a <div> boundary.
    """
    if elem.tag == "p" and elem.get("rend") in SECTION_HEAD_RENDS:
        return True
    if elem.tag == "trailer" or (elem.tag == "p" and elem.get("rend") == "centre"):
        return bool(SECTION_END_RE.search(normalize(clean_text(elem))))
    return False


# A verse's n= is normally a single number, but an abbreviated passage that
# stands in for a run of elided verses carries the whole range, e.g.
# n="225-240". One such paragraph can cover hundreds of verse numbers while
# being only a few lines of text.
VERSE_RANGE_RE = re.compile(r"^(\d+)-(\d+)$")


def parse_verse_number(raw):
    """Parse an n= attribute into (first, last). A plain number gives first == last."""
    raw = raw.strip()
    if raw.isdigit():
        n = int(raw)
        return n, n
    m = VERSE_RANGE_RE.match(raw)
    if not m:
        raise ValueError(f"Unrecognised verse number {raw!r}")
    first, last = int(m.group(1)), int(m.group(2))
    if last < first:
        raise ValueError(f"Reversed verse range {raw!r}")
    return first, last


def verse_label(first, last):
    """How a verse or span of verses is written in output: "12" or "225-240"."""
    return str(first) if first == last else f"{first}-{last}"


def heading_path(div_node, parent_map):
    chain = []
    node = div_node
    while node is not None:
        if node.tag == "div":
            head = node.find("head")
            if head is not None and (head.text or "").strip():
                chain.append(head.text.strip())
        node = parent_map.get(node)
    chain.reverse()
    return chain


def parse_verses(filepath):
    tree = ET.parse(filepath)
    root = tree.getroot()
    parent_map = {child: parent for parent in root.iter() for child in parent}

    verses = []
    current = None
    section_pending = False
    for el in root.iter():
        if starts_new_section(el):
            section_pending = True
            continue
        if el.tag != "p" or el.get("rend") != "bodytext":
            continue
        n = el.get("n")
        text = normalize(clean_text(el))
        if n is not None:
            first, last = parse_verse_number(n)
            current = {
                "n": first,
                "n_last": last,
                "span": last - first + 1,
                "label": verse_label(first, last),
                "parent": parent_map.get(el),
                "texts": [text],
                "section_start": section_pending,
            }
            verses.append(current)
            section_pending = False
        elif current is not None:
            current["texts"].append(text)

    for v in verses:
        v["heading"] = heading_path(v["parent"], parent_map)
    return verses


def select_chunk(files, source_dir, state, chunk_size):
    idx = files.index(state["file"]) if state["file"] in files else 0
    next_verse = state["next_verse"]

    for _ in range(len(files) + 1):
        filename = files[idx]
        verses = parse_verses(os.path.join(source_dir, filename))

        # Match on n_last so a saved position that lands inside a range still
        # resumes at the paragraph covering it rather than skipping past it.
        start_i = next(
            (i for i, v in enumerate(verses) if v["n_last"] >= next_verse), None
        )
        if start_i is None:
            idx = (idx + 1) % len(files)
            next_verse = 1
            continue

        # The budget counts verse numbers, not paragraphs, so a range counts for
        # its whole span. A range is never split: if adding one overshoots
        # CHUNK_SIZE the chunk simply runs long, and the next one picks up after
        # the end of the range.
        chunk = [verses[start_i]]
        span = verses[start_i]["span"]
        parent = verses[start_i]["parent"]
        i = start_i + 1
        while (
            span < chunk_size
            and i < len(verses)
            and verses[i]["parent"] is parent
            and not verses[i]["section_start"]
        ):
            chunk.append(verses[i])
            span += verses[i]["span"]
            i += 1

        if i < len(verses):
            new_state = {"file": filename, "next_verse": verses[i]["n"]}
        else:
            new_idx = (idx + 1) % len(files)
            new_state = {"file": files[new_idx], "next_verse": 1}

        # The chunk completes a section when the verse that would follow it
        # begins a new section — a new parent <div> (khanda/chapter boundary),
        # an intra-div section start, or the end of the file. In that case,
        # gather the whole just-finished section (which may have begun in an
        # earlier run) by walking back to its first verse.
        section_ended = (
            i >= len(verses)
            or verses[i]["parent"] is not parent
            or verses[i]["section_start"]
        )
        section_verses = None
        if section_ended:
            sec_start = start_i
            while (
                sec_start > 0
                and not verses[sec_start]["section_start"]
                and verses[sec_start - 1]["parent"] is verses[sec_start]["parent"]
            ):
                sec_start -= 1
            section_verses = verses[sec_start:i]

        return filename, chunk, chunk[0]["heading"], new_state, section_verses

    raise RuntimeError("No numbered verses found in any source file")


def find_section(verses, verse_n):
    """The whole section containing verse `verse_n`, as a list of verses.

    Used by the summary backfill (see SUMMARIZE_VERSE), to re-summarise a
    section whose boundary an earlier run failed to detect.
    """
    at = next(
        (i for i, v in enumerate(verses) if v["n"] <= verse_n <= v["n_last"]), None
    )
    if at is None:
        raise RuntimeError(f"Verse {verse_n} not found")

    start = at
    while (
        start > 0
        and not verses[start]["section_start"]
        and verses[start - 1]["parent"] is verses[start]["parent"]
    ):
        start -= 1

    end = at + 1
    while (
        end < len(verses)
        and not verses[end]["section_start"]
        and verses[end]["parent"] is verses[at]["parent"]
    ):
        end += 1

    return verses[start:end]


def format_pali(chunk):
    return "\n\n".join(f"[{v['label']}] {' '.join(v['texts'])}" for v in chunk)
