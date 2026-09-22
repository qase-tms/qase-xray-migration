"""Logging for the migration tool, plus the end-of-run migration report.

Two things to know about the shape here.

Handlers are attached to the ROOT logger, not to a named one. Every module gets
its logger with ``get_logger(__name__)``, so their names are ``loaders.qase_loader``,
``services.qase_service`` and so on. Configuring a logger called
``xray_migration`` left every one of those as an unconfigured sibling: they
printed to the console through their own handler and **never reached the log
file**. Attaching to root means propagation carries every module's output to
both destinations, which is what a log file is for.

The report is built from warnings rather than from
hand-placed calls at each skip site. ``ReportHandler`` is an ordinary logging
handler, so a warning added anywhere in the codebase later becomes a report
line by construction, with no change to the 17 modules that log.
"""

import logging
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Five additive logging levels. Python's stdlib has four
# of them; VERBOSE sits between INFO and DEBUG and carries request URIs and
# status codes, leaving DEBUG for full parameters and bodies.
VERBOSE = 15
logging.addLevelName(VERBOSE, "VERBOSE")

LEVELS = {
    "error": logging.ERROR,
    "warn": logging.WARNING,
    "info": logging.INFO,
    "verbose": VERBOSE,
    "debug": logging.DEBUG,
}
_ALIASES = {"warning": "warn", "err": "error", "trace": "debug", "critical": "error"}


def normalise_level(level) -> str:
    """Map a config value to one of the five level names. Unknown means info."""
    if level is None:
        return "info"
    if isinstance(level, bool):
        return "debug" if level else "info"
    if isinstance(level, int):
        for name, value in LEVELS.items():
            if value == level:
                return name
        return "info"
    name = str(level).strip().lower()
    name = _ALIASES.get(name, name)
    return name if name in LEVELS else "info"


def _verbose(self, message, *args, **kwargs):
    if self.isEnabledFor(VERBOSE):
        self._log(VERBOSE, message, args, **kwargs)


logging.Logger.verbose = _verbose


class ReportHandler(logging.Handler):
    """Collects every warning and error as an end-of-run report entry.

    Messages are conventionally prefixed ``[CODE][Entity] ...`` or
    ``[Entity] ...``; parsing that gives per-project grouping for free. A
    single bracket group is an entity, not a project code, because messages
    logged outside a project context look like ``[Projects] ...``.
    """

    _PREFIX = re.compile(r"^\[([^\]]+)\]\s*(?:\[([^\]]+)\]\s*)?")

    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.issues = {}

    @classmethod
    def split_prefix(cls, message: str):
        match = cls._PREFIX.match(message or "")
        if not match:
            return None, None, message
        first, second = match.group(1), match.group(2)
        remainder = message[match.end():]
        if second:
            return first, second, remainder
        return None, first, remainder

    def emit(self, record):
        if record.levelno < logging.WARNING:
            return
        try:
            message = record.getMessage()
        except Exception:
            return
        code, entity, remainder = self.split_prefix(message)
        level = "error" if record.levelno >= logging.ERROR else "warn"
        self.issues.setdefault(code or "-", []).append(
            {"level": level, "entity": entity or "-", "message": (remainder or message).strip()}
        )

    def total(self) -> int:
        return sum(len(v) for v in self.issues.values())

    def print_report(self, max_lines: int = 60, artifacts: Optional[str] = None) -> None:
        """Print what the run skipped, degraded or failed, and why.

        Counts alone can overstate success: a run that quietly dropped a
        thousand attachments still reports a large number of migrated cases.
        When there is nothing to report it says so, because silence is
        indistinguishable from a bug.
        """
        total = self.total()
        if not total:
            print("\n------ Migration report: no skipped or degraded items ------")
            if artifacts:
                print(artifacts)
            return
        print(f"\n------ Migration report: {total} skipped/degraded item(s) ------")
        shown = 0
        for code in sorted(self.issues):
            items = self.issues[code]
            print(f"\n  [{code}] · {len(items)} item(s)")
            for issue in items:
                if shown >= max_lines:
                    print(f"\n  … +{total - shown} more, see the log file")
                    if artifacts:
                        print(artifacts)
                    return
                icon = "✗" if issue["level"] == "error" else "!"
                print(f"    {icon} [{issue['entity']}] {issue['message']}")
                shown += 1
        if artifacts:
            print(artifacts)


# One report per process, so cli.py can print it whatever phase ran.
REPORT = ReportHandler()


def read_version() -> str:
    """Version of this migration, from the VERSION file at the repo root.

    Surfaced in the log header and the end-of-run report so a customer's
    attached log answers "which version are you on?" without anyone asking.
    """
    try:
        path = Path(__file__).resolve().parent.parent / "VERSION"
        return path.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


VERSION_STRING = read_version()


def log_filename(prefix: str = "", log_dir: str = "./logs") -> str:
    """``logs/<prefix>_xray_<YYYYMMDD_HHMMSS>.log`` per """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{prefix}_xray_{stamp}.log" if prefix else f"xray_{stamp}.log"
    return str(Path(log_dir) / name)


def setup_logger(
    name: str = "xray_migration",
    log_file: Optional[str] = None,
    level=logging.INFO,
    write_to_file: bool = True,
) -> logging.Logger:
    """Configure the root logger so every module's output reaches both sinks.

    ``name`` is accepted for backward compatibility and is only used for the
    returned logger; handlers always go on root.
    """
    if isinstance(level, str):
        level = LEVELS[normalise_level(level)]

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(
        logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(console)

    if log_file and write_to_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        # First line of every log: the version, so a customer's attached log
        # answers "which version are you on?" without anyone asking.
        if not path.exists() or path.stat().st_size == 0:
            path.write_text(
                f"# qase-xray-migration v{VERSION_STRING} | started "
                f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
                encoding="utf-8",
            )
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        # The file is always the fuller record: a console kept at info should
        # not cost you the detail you need when something went wrong.
        file_handler.setLevel(min(level, logging.DEBUG) if level <= VERBOSE else logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(file_handler)

    if REPORT not in root.handlers:
        root.addHandler(REPORT)

    return logging.getLogger(name)


def get_logger(name: str = "xray_migration") -> logging.Logger:
    """Module logger. Output propagates to the handlers configured on root."""
    return logging.getLogger(name)
