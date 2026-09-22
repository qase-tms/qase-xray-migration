"""Preflight check, validate config and connectivity BEFORE running the migration.

Run:  python preflight.py [config.json]

Checks, in order:
  1. Config file parses; required keys present, no placeholder values, project
     selection unambiguous
  2. Xray Cloud authentication (POST /api/v2/authenticate) returns a token
  3. Jira credentials, if set, are complete and answer
  4. Qase API auth works (GET /v1/project)

Exit code 0 = all green; 1 = at least one failure.
"""

import sys

# Python 3.11 minimum.
if sys.version_info < (3, 11):
    sys.exit(
        f"This migration requires Python 3.11 or newer "
        f"(found {sys.version_info.major}.{sys.version_info.minor})."
    )

import requests

from utils.config import ConfigError, load_config, qase_api_url, selected_projects, is_dedicated_cluster
from utils.logger import LEVELS, normalise_level

_PLACEHOLDER_MARKERS = ("<", ">", "your-", "YOUR_", "changeme", "xxxx", "yourcompany")
_XRAY_AUTH_URL = "https://xray.cloud.getxray.app/api/v2/authenticate"

_results = []


def _report(name: str, ok: bool, detail: str = ""):
    print(f"  {'✅' if ok else '❌'} {name}" + (f": {detail}" if detail else ""))
    _results.append(ok)


def _warn(name: str, detail: str = ""):
    print(f"  ⚠️  {name}" + (f": {detail}" if detail else ""))


def _looks_placeholder(value: str) -> bool:
    return any(marker in value for marker in _PLACEHOLDER_MARKERS)


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.json"

    print("\n- Config -")
    try:
        config = load_config(config_path)
    except ConfigError as e:
        _report(f"Config file {config_path}", False, str(e))
        return _finish()
    _report(f"Config file {config_path}", True, "parses OK")

    required = {
        "qase.api_token": "Qase API token",
        "xray.client_id": "Xray Cloud client id",
        "xray.client_secret": "Xray Cloud client secret",
        "jira.url": "Jira site URL",
    }
    config_ok = True
    for key, label in required.items():
        value = str(config.get(key) or "").strip()
        if not value:
            _report(f"{key} ({label})", False, "missing/empty")
            config_ok = False
        elif _looks_placeholder(value):
            _report(f"{key} ({label})", False, f"looks like a placeholder: {value[:40]!r}")
            config_ok = False
        else:
            _report(f"{key} ({label})", True)

    import_all = bool(config.get("projects.import_all"))
    projects = [
        p for p in selected_projects(config)
        if not _looks_placeholder(p)
    ]
    if import_all:
        _warn(
            "projects.import_all",
            "every project the Xray credentials can see. Xray has no cheap way to "
            "enumerate them, so the extract phase resolves the list.",
        )
    elif not projects:
        _report(
            "projects.import", False,
            "empty, list the Jira project keys to migrate (or set projects.import_all: true)",
        )
        config_ok = False
    else:
        _report("projects.import", True, f"{len(projects)} project key(s): {projects}")

    level = normalise_level(config.get("logging.level"))
    if config.get("logging.level") and level == "info" and str(config.get("logging.level")).lower() != "info":
        _warn(
            f"logging.level {config.get('logging.level')!r} is not recognised",
            f"falling back to 'info'; valid: {sorted(LEVELS)}",
        )

    if not config_ok:
        return _finish()

    # ---------------- Xray Cloud ----------------
    print("\n- Xray Cloud -")
    try:
        resp = requests.post(
            _XRAY_AUTH_URL,
            json={
                "client_id": str(config.get("xray.client_id")),
                "client_secret": str(config.get("xray.client_secret")),
            },
            timeout=(15, 30),
        )
        if resp.status_code == 200 and resp.text.strip():
            _report("Xray authentication", True, "token issued")
        else:
            _report(
                "Xray authentication", False,
                f"HTTP {resp.status_code}: {resp.text[:200]}",
            )
            _warn(
                "Hint",
                "Generate the pair in Jira under Apps > Xray > API Keys. A 401 here "
                "means the client id or secret is wrong, not that Xray is down.",
            )
            return _finish()
    except requests.exceptions.RequestException as e:
        _report("Xray authentication", False, str(e)[:200])
        return _finish()

    # ---------------- Jira (attachments and issue keys) ----------------
    print("\n- Jira -")
    jira_url = str(config.get("jira.url") or "").rstrip("/")
    email = str(config.get("jira.email") or "").strip()
    token = str(config.get("jira.api_token") or "").strip()
    if not (email and token):
        _warn(
            "Jira credentials not set",
            "attachment downloads and issue-key resolution will be skipped. "
            "Xray data itself is unaffected.",
        )
    else:
        try:
            resp = requests.get(
                f"{jira_url}/rest/api/3/myself",
                auth=(email, token),
                timeout=(15, 30),
            )
            if resp.status_code == 200:
                _report("Jira auth (GET /rest/api/3/myself)", True, email)
            else:
                _report(
                    "Jira auth (GET /rest/api/3/myself)", False,
                    f"HTTP {resp.status_code}: {resp.text[:160]}",
                )
        except requests.exceptions.RequestException as e:
            _report("Jira auth", False, str(e)[:200])

    # ---------------- Qase ----------------
    print("\n- Qase -")
    qase_host = str(config.get("qase.host") or "qase.io")
    api_url = qase_api_url(config)
    if is_dedicated_cluster(qase_host):
        _report("Qase host", True, f"{qase_host} treated as a dedicated cluster: {api_url}")
    try:
        resp = requests.get(
            f"{api_url}/v1/project",
            headers={"Token": str(config.get("qase.api_token"))},
            params={"limit": 1},
            timeout=(15, 30),
        )
        if resp.status_code == 200 and (resp.json() or {}).get("status"):
            total = ((resp.json().get("result") or {}).get("total")) or 0
            _report("Qase auth (GET /v1/project)", True, f"{total} project(s) in workspace")
        else:
            _report("Qase auth (GET /v1/project)", False, f"HTTP {resp.status_code}: {resp.text[:200]}")
    except requests.exceptions.RequestException as e:
        _report("Qase auth (GET /v1/project)", False, str(e)[:200])

    return _finish()


def _finish():
    failed = _results.count(False)
    print()
    if failed:
        print(f"❌ Preflight FAILED, {failed} check(s) failed. Fix the items above before migrating.")
        sys.exit(1)
    print("✅ Preflight passed, ready to run: python cli.py migrate")
    sys.exit(0)


if __name__ == "__main__":
    main()
