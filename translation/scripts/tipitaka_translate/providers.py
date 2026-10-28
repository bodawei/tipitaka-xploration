"""LLM provider API clients and provider selection.

Each provider speaks its own HTTP protocol but is invoked through the single
`chat_message` dispatcher. `load_active_providers` resolves the enabled set
from the provider state file and the optional per-run PROVIDERS override.
"""
import json
import os

from .config import PROVIDERS
from .http import post_json

CLAUDE_URL = "https://api.anthropic.com/v1/messages"
KIMI_URL = "https://api.moonshot.ai/v1/chat/completions"
DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


def anthropic_message(api_key, model, system, user_msg, truncation_hint):
    result = post_json(
        CLAUDE_URL,
        {
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        {
            "model": model,
            "max_tokens": 16000,
            "system": system,
            "messages": [{"role": "user", "content": user_msg}],
        },
        120,
    )

    if result.get("stop_reason") == "max_tokens":
        raise RuntimeError(truncation_hint)

    return "".join(block["text"] for block in result["content"] if block["type"] == "text")


def kimi_message(api_key, model, system, user_msg, truncation_hint):
    """Call the Moonshot (Kimi) chat-completions API, which is OpenAI-compatible."""
    result = post_json(
        KIMI_URL,
        {
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
        {
            "model": model,
            "max_completion_tokens": 16000,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
        },
        180,
    )

    choice = result["choices"][0]
    if choice.get("finish_reason") == "length":
        raise RuntimeError(truncation_hint)

    content = choice["message"]["content"]
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if part.get("type") == "text"
        )
    return content


def deepseek_message(api_key, model, system, user_msg, truncation_hint):
    """Call the DeepSeek chat-completions API, which is OpenAI-compatible.

    Thinking mode is disabled: this job wants the translation itself, not the
    model's reasoning trace, and it keeps the response faster and cheaper.
    """
    result = post_json(
        DEEPSEEK_URL,
        {
            "content-type": "application/json",
            "authorization": f"Bearer {api_key}",
        },
        {
            "model": model,
            "max_tokens": 16000,
            "thinking": {"type": "disabled"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
        },
        180,
    )

    choice = result["choices"][0]
    if choice.get("finish_reason") == "length":
        raise RuntimeError(truncation_hint)

    content = choice["message"]["content"]
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if part.get("type") == "text"
        )
    return content or ""


def chat_message(provider, api_key, model, system, user_msg, truncation_hint):
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

    active = []
    for name in enabled:
        cfg = PROVIDERS[name]
        key = os.environ.get(cfg["key_env"])
        if not key:
            raise RuntimeError(
                f"Environment variable {cfg['key_env']} is not set "
                f"(required for provider {name!r})"
            )
        active.append({
            "name": name,
            "label": cfg["label"],
            "model": os.environ.get(cfg["model_env"], cfg["model_default"]),
            "key": key,
        })
    return active
