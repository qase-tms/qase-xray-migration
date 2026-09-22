"""Dry-run Qase service, reads hit the real API, writes are logged and faked.

Used by ``--dry-run`` on the load and migrate phases: extraction and transform
run in full, so unmapped statuses, missing fields and attachment problems all
surface in the migration report, but nothing is created in Qase. Fake ids keep
the mappings consistent so the rest of the load still executes end to end.
"""

import itertools
import os

from utils.logger import get_logger

from .qase_service import QaseService

logger = get_logger(__name__)


class DryRunQaseService(QaseService):
    _fake_ids = itertools.count(10_000_000)

    @staticmethod
    def _dry(message: str) -> None:
        logger.info("[DRY-RUN] %s", message)

    # ---- writes: logged, never sent ----------------------------------

    def create_project(self, project_data):
        code = (project_data or {}).get("code")
        self._dry(f"would create project {(project_data or {}).get('title')!r} [{code}]")
        return {"code": code, "id": next(self._fake_ids)}

    def upload_attachment(self, project_code, file_path):
        name = os.path.basename(str(file_path))
        self._dry(f"[{project_code}] would upload attachment {name!r}")
        return {"hash": f"dry-run-{next(self._fake_ids)}", "filename": name}

    def create_suite(self, project_code, suite_data):
        self._dry(f"[{project_code}] would create suite {(suite_data or {}).get('title')!r}")
        return {"id": next(self._fake_ids)}

    def create_cases_bulk(self, project_code, cases):
        self._dry(f"[{project_code}] would bulk-create {len(cases or [])} case(s)")
        return {"status": "success", "ids": [next(self._fake_ids) for _ in (cases or [])]}

    def create_run(self, project_code, run_data):
        self._dry(f"[{project_code}] would create run {(run_data or {}).get('title')!r}")
        return {"id": next(self._fake_ids)}

    def create_results_bulk_v2(self, project_code, run_id, results):
        self._dry(
            f"[{project_code}] would send {len(results or [])} result(s) to run {run_id}"
        )
        return {"status": "success"}

    def complete_run(self, project_code, run_id):
        self._dry(f"[{project_code}] would complete run {run_id}")
        return {"status": "success"}
