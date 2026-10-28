"""Translation provider registry.

Each provider names the environment variables that carry its API key and model
override, plus the model used when the override is unset. Which providers run
each day is controlled by booleans in the provider state file
(translation/state/providers.json by default); the optional PROVIDERS
environment variable can narrow that set for one run.

Together the active providers translate the same chunk in lockstep, keeping
separate state, glossaries, and archives so their output can be compared.
"""

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
    "deepseek": {
        "label": "DeepSeek",
        "key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "model_default": "deepseek-flash",
    },
}
