"""Per-provider progress state (path derivation, loading, lockstep, saving)
and the record of a submitted-but-uncollected batch."""
import json
import os


def state_path_for(state_base, provider):
    """Per-provider progress state path derived from the STATE_FILE base."""
    root, ext = os.path.splitext(state_base)
    return f"{root}-{provider}{ext}"


def load_state(state_base, provider):
    path = state_path_for(state_base, provider)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def require_lockstep(states, active, state_base):
    """Return the shared saved position, refusing to run if providers disagree.

    Providers translate the same chunk in lockstep, so their saved positions
    must agree. Divergence means an earlier run used a provider subset and the
    two streams are out of sync — refuse to produce a mismatched side-by-side
    comparison.
    """
    state = states[active[0]["name"]]
    if len(active) > 1:
        for p in active[1:]:
            if states[p["name"]] != state:
                raise RuntimeError(
                    f"Saved positions disagree between providers: "
                    f"{active[0]['name']} at {state}, "
                    f"{p['name']} at {states[p['name']]} "
                    f"({state_path_for(state_base, p['name'])}). Run a single "
                    f"provider until they agree, or fix the state files."
                )
    return state


def save_state(state_base, provider, state):
    path = state_path_for(state_base, provider)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def load_pending(path):
    """The batch submitted by an earlier run and not yet collected, or None.

    Shape: {"submitted_at", "position": <saved state the chunk was selected
    from>, "chunk_size", "file", "verse_range",
    "batches": {provider: {"id", "model"}}}.
    """
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_pending(path, record):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
        f.write("\n")


def clear_pending(path):
    if os.path.exists(path):
        os.remove(path)
