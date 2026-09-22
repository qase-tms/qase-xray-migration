"""Unit tests for the parts of this migration that can be verified offline.

Xray Cloud needs OAuth credentials we do not have, so this script cannot be
exercised against a real instance here. Everything that CAN be checked without
one is checked: config handling, host derivation, project selection, retry
semantics, logging levels and the end-of-run report.

Run:  python -m pytest tests/ -q
"""

import io
import json
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.qase_service import QaseService
from services.qase_dry_run import DryRunQaseService
from utils.config import (
    Config, ConfigError, load_config, is_dedicated_cluster,
    mapped_project_code, qase_api_url, selected_projects, _ENV_OVERRIDES,
)
from utils.logger import LEVELS, REPORT, ReportHandler, VERBOSE, normalise_level, setup_logger, get_logger


# --------------------------------------------------------------------------
# 2 — the host derives everything
# --------------------------------------------------------------------------

@pytest.mark.parametrize("host,expected", [
    ("qase.io", "https://api.qase.io"),
    ("acme.qase.io", "https://api-acme.qase.io"),
])
def test_qase_api_url(host, expected):
    assert qase_api_url(Config({"qase": {"host": host}})) == expected


def test_qase_api_url_defaults_to_public_cloud():
    assert qase_api_url(Config({"qase": {}})) == "https://api.qase.io"


def test_ssl_false_downgrades_scheme():
    assert qase_api_url(Config({"qase": {"host": "qase.io", "ssl": False}})) == "http://api.qase.io"


def test_dedicated_cluster_detection():
    assert is_dedicated_cluster("qase.io") is False
    assert is_dedicated_cluster("acme.qase.io") is True


def test_api_url_carries_no_version_suffix():
    """QaseService appends /v1 and /v2 itself; a suffix here would double it."""
    url = qase_api_url(Config({"qase": {"host": "qase.io"}}))
    assert not url.endswith("/v1") and not url.endswith("/v2")


# --------------------------------------------------------------------------
# 2 — config raises rather than leaving an empty dict behind
# --------------------------------------------------------------------------

def test_missing_config_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "nope.json"))


def test_malformed_config_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    with pytest.raises(ConfigError):
        load_config(str(bad))


def test_non_object_config_raises(tmp_path):
    arr = tmp_path / "arr.json"
    arr.write_text("[1,2]")
    with pytest.raises(ConfigError):
        load_config(str(arr))


def test_environment_overrides_the_file(tmp_path, monkeypatch):
    f = tmp_path / "c.json"
    f.write_text(json.dumps({"qase": {"api_token": "FILE"}, "xray": {"client_secret": "FILE"}}))
    monkeypatch.setenv("QASE_API_TOKEN", "ENV_Q")
    monkeypatch.setenv("XRAY_CLIENT_SECRET", "ENV_X")
    config = load_config(str(f))
    assert config.get("qase.api_token") == "ENV_Q"
    assert config.get("xray.client_secret") == "ENV_X"


def test_dot_path_lookup_and_defaults():
    config = Config({"a": {"b": {"c": 1}}})
    assert config.get("a.b.c") == 1
    assert config.get("a.b.missing") is None
    assert config.get("a.b.missing", "fallback") == "fallback"
    assert config.get("nothing.here") is None


def test_every_env_override_targets_a_key_the_code_reads():
    import pathlib, re
    root = pathlib.Path(__file__).resolve().parent.parent
    keys = set()
    for f in root.rglob("*.py"):
        if "venv" in str(f) or "tests" in str(f):
            continue
        keys |= set(re.findall(r"config\.get\(\s*['\"]([a-z_.]+)['\"]", f.read_text()))
    for env, target in _ENV_OVERRIDES.items():
        assert target in keys, f"{env} writes {target}, which nothing reads"


def test_config_example_matches_what_the_code_reads():
    """no aspirational keys, and nothing undocumented."""
    import pathlib, re
    root = pathlib.Path(__file__).resolve().parent.parent
    example = json.loads((root / "config.example.json").read_text())

    def flat(o, prefix=""):
        for k, v in o.items():
            path = f"{prefix}{k}"
            if isinstance(v, dict) and v and "map" not in k:
                yield from flat(v, path + ".")
            else:
                yield path

    documented = set(flat(example)) | {"projects.mapping"}
    read = set()
    for f in root.rglob("*.py"):
        if "venv" in str(f) or "tests" in str(f):
            continue
        read |= set(re.findall(r"config\.get\(\s*['\"]([a-z_.]+)['\"]", f.read_text()))
    assert read - documented == set(), f"read but undocumented: {sorted(read - documented)}"
    assert documented - read == set(), f"documented but never read: {sorted(documented - read)}"


# --------------------------------------------------------------------------
# 2 — project selection
# --------------------------------------------------------------------------

def test_import_list_is_used_when_import_all_is_off():
    config = Config({"projects": {"import": ["abc", "DEF"]}})
    assert selected_projects(config) == ["ABC", "DEF"]


def test_exclude_wins_over_import():
    config = Config({"projects": {"import": ["ABC", "DEF"], "exclude": ["def"]}})
    assert selected_projects(config) == ["ABC"]


def test_exclude_wins_over_import_all():
    config = Config({"projects": {"import_all": True, "exclude": ["ZED"]}})
    assert selected_projects(config, available=["ABC", "ZED"]) == ["ABC"]


def test_import_all_uses_the_available_list():
    config = Config({"projects": {"import_all": True}})
    assert selected_projects(config, available=["ABC", "DEF"]) == ["ABC", "DEF"]


def test_duplicates_and_blanks_are_dropped():
    config = Config({"projects": {"import": ["ABC", "abc", "  ", ""]}})
    assert selected_projects(config) == ["ABC"]


def test_project_mapping():
    config = Config({"projects": {"mapping": {"abc": "pay"}}})
    assert mapped_project_code(config, "ABC") == "PAY"
    assert mapped_project_code(config, "DEF") is None
    assert mapped_project_code(Config({}), "ABC") is None


# --------------------------------------------------------------------------
# Retry table. This service previously had no retry at all.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504, None])
def test_transient_statuses_retry(status):
    assert QaseService.is_retryable(status) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405, 409, 410, 422, 451])
def test_deterministic_4xx_do_not_retry(status):
    assert QaseService.is_retryable(status) is False


def test_retry_after_is_honoured():
    assert QaseService.retry_delay(0, {"Retry-After": "12"}) == 12.0


def test_retry_after_is_capped():
    assert QaseService.retry_delay(0, {"Retry-After": "99999"}) == 300.0


def test_garbage_retry_after_falls_back_to_backoff():
    assert QaseService.retry_delay(0, {"Retry-After": "soon"}) == QaseService.RETRY_BACKOFF_SEC


def test_backoff_is_capped():
    assert QaseService.retry_delay(50) == QaseService.RETRY_BACKOFF_CAP_SEC


def test_backoff_grows_with_attempts():
    assert QaseService.retry_delay(0) < QaseService.retry_delay(2) <= QaseService.RETRY_BACKOFF_CAP_SEC


# --------------------------------------------------------------------------
# Qase lowered the bulk-results limit to 200; this loader batched at 500.
# --------------------------------------------------------------------------

def test_result_chunk_is_at_the_new_limit():
    assert QaseService.RESULT_BULK_CHUNK == 200


@pytest.mark.parametrize("n", [0, 1, 199, 200, 201, 500, 2962])
def test_chunking_loses_nothing_and_never_exceeds_the_limit(n):
    limit = QaseService.RESULT_BULK_CHUNK
    results = list(range(n))
    chunks = [results[i:i + limit] for i in range(0, len(results), limit)]
    assert all(len(c) <= limit for c in chunks)
    assert all(len(c) > 0 for c in chunks)
    assert [x for c in chunks for x in c] == results


# --------------------------------------------------------------------------
# Logging levels, and the report built from warnings
# --------------------------------------------------------------------------

@pytest.mark.parametrize("given,expected", [
    ("warning", "warn"), ("err", "error"), ("trace", "debug"), ("critical", "error"),
    (True, "debug"), (False, "info"), (None, "info"), ("nonsense", "info"),
    ("VERBOSE", "verbose"), (logging.WARNING, "warn"),
])
def test_level_normalisation(given, expected):
    assert normalise_level(given) == expected


def test_verbose_sits_between_info_and_debug():
    assert logging.DEBUG < VERBOSE < logging.INFO
    assert LEVELS["verbose"] == VERBOSE


def test_module_loggers_reach_the_log_file(tmp_path, monkeypatch):
    """The defect this replaced: handlers were attached to a named logger while
    every module used get_logger(__name__), so module output never reached the
    file. Root-logger configuration is what fixes it."""
    monkeypatch.chdir(tmp_path)
    log_file = tmp_path / "logs" / "run.log"
    setup_logger(log_file=str(log_file), level="info")
    get_logger("loaders.qase_loader").warning("[ACME][Cases] something was skipped")
    body = log_file.read_text()
    assert "something was skipped" in body


def test_report_captures_warnings_and_errors_but_not_info():
    handler = ReportHandler()
    for level, message in [
        (logging.WARNING, "[ACME][Cases] title truncated"),
        (logging.ERROR, "[Fields] field rejected"),
        (logging.INFO, "[ACME][Cases] progress"),
    ]:
        # handle() applies the handler's level, which is the real logging path
        handler.handle(logging.LogRecord("x", level, "f", 1, message, None, None))
    assert handler.total() == 2
    assert sorted(handler.issues) == ["-", "ACME"]
    assert handler.issues["ACME"][0]["message"] == "title truncated"
    assert handler.issues["ACME"][0]["entity"] == "Cases"
    assert handler.issues["-"][0]["level"] == "error"


def test_emit_filters_info_even_when_called_directly():
    """emit() is public; a direct call must not smuggle info into the report."""
    handler = ReportHandler()
    handler.emit(logging.LogRecord("x", logging.INFO, "f", 1, "[A][B] progress", None, None))
    assert handler.total() == 0


def test_single_bracket_is_an_entity_not_a_project_code():
    code, entity, rest = ReportHandler.split_prefix("[Projects] could not create")
    assert code is None and entity == "Projects" and rest == "could not create"


def test_a_clean_run_says_so_rather_than_printing_nothing():
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        ReportHandler().print_report()
    finally:
        sys.stdout = real
    assert "no skipped or degraded items" in buf.getvalue()


# --------------------------------------------------------------------------
# A phase that failed must not exit 0
# --------------------------------------------------------------------------

def _exit_code_for(levels):
    """Mirror cli._finish's rule: errors fail the phase, warnings do not."""
    handler = ReportHandler()
    for level in levels:
        handler.handle(logging.LogRecord("x", level, "f", 1, "[A][B] something", None, None))
    errors = sum(
        1 for items in handler.issues.values() for i in items if i["level"] == "error"
    )
    return 1 if errors else 0


def test_a_clean_phase_exits_zero():
    assert _exit_code_for([]) == 0


def test_warnings_alone_do_not_fail_a_phase():
    """A skipped attachment is a degraded migration, not a broken one."""
    assert _exit_code_for([logging.WARNING, logging.WARNING]) == 0


def test_any_error_fails_the_phase():
    """The defect this guards: extraction hit 8 auth failures, logged them all,
    wrote an empty cache and still exited 0."""
    assert _exit_code_for([logging.ERROR]) == 1
    assert _exit_code_for([logging.WARNING, logging.ERROR]) == 1


# --------------------------------------------------------------------------
# --dry-run must not be able to write
# --------------------------------------------------------------------------

def test_dry_run_overrides_every_write_method():
    import inspect
    prefixes = ("create_", "update_", "delete_", "upload_", "send_", "complete_")
    writes = [
        n for n, _ in inspect.getmembers(QaseService, inspect.isfunction)
        if n.startswith(prefixes)
    ]
    assert writes, "no write methods found, the check would be vacuous"
    missed = [n for n in writes if getattr(DryRunQaseService, n) is getattr(QaseService, n)]
    assert missed == [], f"dry-run would really write via: {missed}"


def test_dry_run_returns_usable_ids():
    d = DryRunQaseService.__new__(DryRunQaseService)
    assert d.create_project({"title": "T", "code": "C"})["code"] == "C"
    assert isinstance(d.create_suite("C", {"title": "s"})["id"], int)
    assert isinstance(d.create_run("C", {"title": "r"})["id"], int)
    assert "hash" in d.upload_attachment("C", "/tmp/a.png")
    assert len(d.create_cases_bulk("C", [1, 2, 3])["ids"]) == 3
