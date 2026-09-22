"""Configuration loading, shared by every CLI phase.

The config is nested and dot-path addressed, matching the other Qase migration
repos, so one set of instructions works across all of them. It used to be flat
(``client_id``, ``qase_host``, ``jira_url``); a customer writes this file by
hand, and it is the most visible surface there is.
"""

import json
import os
from typing import Any, Dict, List

# Secrets may be supplied by environment variable instead of config.json, and
# the environment always wins. This keeps tokens out of a file the customer
# might paste into a support ticket.2.
_ENV_OVERRIDES = {
    "QASE_API_TOKEN": "qase.api_token",
    "XRAY_CLIENT_SECRET": "xray.client_secret",
    "JIRA_API_TOKEN": "jira.api_token",
}

# The public cloud. Any other host is a dedicated cluster, which Qase serves at
# api-<host> rather than api.<host>. That fact is implied by qase.host, so
# there is no separate flag for it.2.
_PUBLIC_CLOUD_HOST = "qase.io"


class ConfigError(Exception):
    """Raised when config.json is missing or unparseable.

    Both cases fail here rather than leaving an empty config behind, so a typo
    surfaces immediately instead of as a 401 half an hour into a migration.
    """


class Config:
    """Dot-path view over the parsed config file."""

    def __init__(self, data: Dict[str, Any]):
        self._data = data

    def get(self, key: str, default=None):
        """``config.get("qase.api_token")``. Missing keys return ``default``."""
        current = self._data
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def set(self, key: str, value) -> None:
        current = self._data
        parts = key.split(".")
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value

    def as_dict(self) -> Dict[str, Any]:
        return self._data

    # dict-style access, so `config["projects"]` keeps working where it reads
    # a whole block rather than a leaf.
    def __getitem__(self, key):
        return self._data[key]

    def __contains__(self, key):
        return key in self._data


def load_config(path: str = "config.json") -> Config:
    """Read and validate config.json, then apply environment overrides."""
    if not os.path.exists(path):
        raise ConfigError(
            f"Config file not found: {path}. "
            f"Copy config.example.json to config.json and fill in your tokens."
        )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Config file {path} is not valid JSON: {e}") from e
    except OSError as e:
        raise ConfigError(f"Config file {path} could not be read: {e}") from e

    if not isinstance(data, dict):
        raise ConfigError(
            f"Config file {path} must contain a JSON object, got {type(data).__name__}"
        )

    config = Config(data)
    for env_var, key in _ENV_OVERRIDES.items():
        value = os.environ.get(env_var)
        if value is not None and value.strip():
            config.set(key, value.strip())
    return config


def is_dedicated_cluster(host: str) -> bool:
    return bool(host) and str(host).strip().lower() != _PUBLIC_CLOUD_HOST


def qase_api_url(config: Config) -> str:
    """Base URL for the Qase REST API, derived from qase.host.

    Returns the origin without a version suffix; QaseService appends /v1 and
    /v2 itself.
    """
    host = str(config.get("qase.host") or _PUBLIC_CLOUD_HOST).strip()
    scheme = "http://" if config.get("qase.ssl") is False else "https://"
    delimiter = "-" if is_dedicated_cluster(host) else "."
    return f"{scheme}api{delimiter}{host}"


def selected_projects(config: Config, available: List[str] = None) -> List[str]:
    """Resolve projects.import_all + projects.import + projects.exclude.

    Matching is case-insensitive on the Jira project key. ``projects.exclude``
    always wins, so it works whether the candidate list came from import_all or
    from import. ``available`` is the set of keys on the tenant, required when
    import_all is set.
    """
    exclude = {
        str(p).strip().upper()
        for p in (config.get("projects.exclude") or [])
        if str(p).strip()
    }
    if config.get("projects.import_all"):
        candidates = [str(p).strip().upper() for p in (available or []) if str(p).strip()]
    else:
        candidates = [
            str(p).strip().upper()
            for p in (config.get("projects.import") or [])
            if str(p).strip()
        ]
    seen, out = set(), []
    for key in candidates:
        if key in exclude or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def mapped_project_code(config: Config, project_key: str):
    """``projects.mapping``: Jira project key to a specific Qase project code."""
    raw = config.get("projects.mapping")
    if not isinstance(raw, dict) or not raw:
        return None
    lookup = {str(k).strip().upper(): v for k, v in raw.items()}
    value = lookup.get(str(project_key or "").strip().upper())
    if value is None:
        return None
    code = str(value).strip().upper()
    return code or None
