"""LLM provider API clients and provider selection.

Each provider speaks its own HTTP protocol but is invoked through the single
`chat_message` dispatcher. `load_active_providers` resolves the enabled set
from the provider state file and the optional per-run PROVIDERS override.
"""
import json
import os

from .config import PROVIDERS
from .http import post_json

ANTHROPIC_BASE = "https://api.anthropic.com/v1"
KIMI_BASE = "https://api.moonshot.ai/v1"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

MAX_OUTPUT_TOKENS = 16000


def anthropic_headers(api_key):
    return {
        "content-type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }


def bearer_headers(api_key):
    return {
        "content-type": "application/json",
        "authorization": f"Bearer {api_key}",
    }


def claude_reasoning_params(model):
    """The cheapest reasoning setting each Claude model accepts.

    Verse translation does not benefit from extended thinking, and thinking
    tokens are billed as output. Models differ in what they accept:
    Claude Opus 5.5 and the Fable/Mythos models cannot disable thinking at all
    (a 400), so the most we can do is the lowest effort; Claude Sonnet 5.5
    rejects "disabled" but treats "between_tools" as thinking-off when, as
    here, no tools are in play; every other model accepts "disabled".
    """
    if model.startswith(("claude-opus-5-5", "claude-fable", "claude-mythos")):
        return {"output_config": {"effort": "low"}}
    if model.startswith("claude-sonnet-5-5"):
        return {"thinking": {"type": "between_tools"}}
    return {"thinking": {"type": "disabled"}}


def anthropic_params(model, system, user_msg):
    """Messages API request body, shared by direct and batch requests."""
    return {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user_msg}],
        **claude_reasoning_params(model),
    }


def parse_anthropic(result, truncation_hint):
    """Return (text, usage) from a Messages API response."""
    stop = result.get("stop_reason")
    if stop == "max_tokens":
        raise RuntimeError(truncation_hint)
    if stop == "refusal":
        raise RuntimeError(f"Claude declined the request: {result.get('stop_details')}")
    text = "".join(
        block["text"] for block in result["content"] if block["type"] == "text"
    )
    return text, result.get("usage") or {}


def chat_completion_body(provider, model, system, user_msg):
    """OpenAI-compatible request body for Kimi and DeepSeek, shared by direct
    and batch requests. Thinking is disabled on both: this job wants the
    translation itself, not a billed reasoning trace."""
    body = {
        "model": model,
        "thinking": {"type": "disabled"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
    }
    if provider == "kimi":
        body["max_completion_tokens"] = MAX_OUTPUT_TOKENS
    else:
        body["max_tokens"] = MAX_OUTPUT_TOKENS
    return body


def parse_chat_completion(result, truncation_hint):
    """Return (text, usage) from an OpenAI-compatible chat completion."""
    choice = result["choices"][0]
    if choice.get("finish_reason") == "length":
        raise RuntimeError(truncation_hint)

    content = choice["message"]["content"]
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if part.get("type") == "text"
        )
    return content or "", result.get("usage") or {}


def anthropic_message(api_key, model, system, user_msg, truncation_hint):
    result = post_json(
        f"{ANTHROPIC_BASE}/messages",
        anthropic_headers(api_key),
        anthropic_params(model, system, user_msg),
        120,
    )
    return parse_anthropic(result, truncation_hint)


def kimi_message(api_key, model, system, user_msg, truncation_hint):
    """Call the Moonshot (Kimi) chat-completions API, which is OpenAI-compatible."""
    result = post_json(
        f"{KIMI_BASE}/chat/completions",
        bearer_headers(api_key),
        chat_completion_body("kimi", model, system, user_msg),
        180,
    )
    return parse_chat_completion(result, truncation_hint)


def deepseek_message(api_key, model, system, user_msg, truncation_hint):
    """Call the DeepSeek chat-completions API, which is OpenAI-compatible."""
    result = post_json(
        DEEPSEEK_URL,
        bearer_headers(api_key),
        chat_completion_body("deepseek", model, system, user_msg),
        180,
    )
    return parse_chat_completion(result, truncation_hint)


def chat_message(provider, api_key, model, system, user_msg, truncation_hint):
    """Make one synchronous request; returns (text, usage)."""
    if provider == "claude":
        return anthropic_message(api_key, model, system, user_msg, truncation_hint)
    if provider == "kimi":
        return kimi_message(api_key, model, system, user_msg, truncation_hint)
    if provider == "deepseek":
        return deepseek_message(api_key, model, system, user_msg, truncation_hint)
    raise ValueError(f"Unknown provider {provider!r}")


def load_provider_toggles(path):
    """Read the per-provider on/off booleans from the provider state file.

    The file is a flat object of provider name -> boolean, e.g.
    {"claude": true, "kimi": true, "deepseek": false}. A provider missing from
    the file is treated as off, so adding a new provider to the registry does
    not silently start spending money on it. If the file does not exist at all,
    every provider is enabled (the historical behaviour).
    """
    if not os.path.exists(path):
        return {name: True for name in PROVIDERS}

    with open(path, encoding="utf-8") as f:
        toggles = json.load(f)

    if not isinstance(toggles, dict):
        raise RuntimeError(f"{path} must contain a JSON object of provider name -> boolean")

    unknown = sorted(set(toggles) - set(PROVIDERS))
    if unknown:
        raise RuntimeError(
            f"Unknown provider(s) in {path}: {', '.join(unknown)}; "
            f"expected one of: {', '.join(sorted(PROVIDERS))}"
        )
    for name, enabled in toggles.items():
        if not isinstance(enabled, bool):
            raise RuntimeError(
                f"Provider toggle {name!r} in {path} must be true or false, "
                f"not {enabled!r}"
            )
    return {name: toggles.get(name, False) for name in PROVIDERS}


def load_active_providers(toggles):
    """Resolve the enabled providers into an ordered list of
    {"name", "label", "model", "key"} dicts.

    Providers are enabled by the booleans in the provider state file. The
    optional PROVIDERS environment variable (whitespace-separated names) can
    narrow that set for one run, but it cannot re-enable a provider that is
    switched off in the state file. Returns [] when nothing is enabled.
    """
    enabled = [name for name in PROVIDERS if toggles[name]]
    if not enabled:
        return []

    spec = os.environ.get("PROVIDERS", "").split()
    if spec:
        seen = set()
        for name in spec:
            if name not in PROVIDERS:
                raise RuntimeError(
                    f"Unknown provider {name!r} in PROVIDERS; "
                    f"expected one of: {', '.join(sorted(PROVIDERS))}"
                )
            if name in seen:
                raise RuntimeError(f"Duplicate provider {name!r} in PROVIDERS")
            if not toggles[name]:
                raise RuntimeError(
                    f"Provider {name!r} is disabled in the provider state file; "
                    f"enable it there or remove it from PROVIDERS"
                )
            seen.add(name)
        enabled = [name for name in enabled if name in seen]

    return [provider_config(name) for name in enabled]


def provider_config(name, model=None):
    """Resolve one provider into a {"name", "label", "model", "key"} dict.

    `model` overrides the environment, for collecting a batch that was
    submitted with a model the environment may since have changed. Model names
    are stripped, so a stray space in a repository variable cannot break a run.
    """
    cfg = PROVIDERS[name]
    key = os.environ.get(cfg["key_env"])
    if not key:
        raise RuntimeError(
            f"Environment variable {cfg['key_env']} is not set "
            f"(required for provider {name!r})"
        )
    if model is None:
        model = os.environ.get(cfg["model_env"], "").strip() or cfg["model_default"]
    return {"name": name, "label": cfg["label"], "model": model, "key": key}
