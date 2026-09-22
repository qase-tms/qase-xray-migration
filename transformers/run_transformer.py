"""Transformer for Xray test executions and runs to Qase runs and results."""

from typing import Dict, Any, List, Tuple, Optional, Set
from datetime import datetime
from utils.cache_manager import CacheManager
from utils.logger import get_logger
from models.mappings import MappingStore
from transformers.xray_transformer import BaseTransformer

logger = get_logger(__name__)


class RunTransformer(BaseTransformer):
    """Transforms Xray test executions to Qase runs and results."""
    
    def transform(
        self,
        executions_data: List[Dict[str, Any]],
        qase_cases: Dict[str, List[Dict[str, Any]]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Transform Xray test executions to Qase runs and results.
        
        Args:
            executions_data: List of Xray test execution data
            qase_cases: Dictionary mapping project_code → list of cases
        
        Returns:
            Tuple of (list of run payloads, list of result payloads)
        """
        qase_runs = []
        qase_results = []
        
        # Xray test issue ids we actually migrate (load maps them → Qase case ids)
        migrated_test_ids: Dict[str, Set[str]] = {}
        for project_code, cases in qase_cases.items():
            migrated_test_ids[project_code] = set()
            for case in cases:
                xray_id = case.get("_xray_issue_id")
                if xray_id is not None:
                    migrated_test_ids[project_code].add(str(xray_id))
        
        for execution in executions_data:
            try:
                project_id = str(execution.get("projectId", ""))
                project_code = self.mappings.get_qase_id(project_id, "project")
                
                if not project_code:
                    continue
                
                # Transform execution to run
                qase_run = self._transform_execution_to_run(execution, project_code)
                if not qase_run:
                    continue
                
                qase_runs.append(qase_run)
                
                tr = execution.get("testRuns")
                test_runs = tr.get("results", []) if isinstance(tr, dict) else []
                for test_run in test_runs:
                    qase_result = self._transform_test_run_to_result(
                        test_run,
                        execution,
                        project_code,
                        migrated_test_ids.get(project_code, set()),
                    )
                    
                    if qase_result:
                        qase_results.append(qase_result)
                
            except Exception as e:
                self.logger.error(f"Error transforming execution {execution.get('issueId')}: {e}")
                continue
        
        self.logger.info(f"Transformed {len(qase_runs)} runs and {len(qase_results)} results")
        return qase_runs, qase_results
    
    def _transform_execution_to_run(
        self,
        execution: Dict[str, Any],
        project_code: str
    ) -> Optional[Dict[str, Any]]:
        """
        Transform a single Xray execution to Qase run.
        
        Args:
            execution: Xray test execution data
            project_code: Qase project code
        
        Returns:
            Qase run payload or None if transformation fails
        """
        jira_data = execution.get("jira") or {}
        summary = jira_data.get("summary", "") or ""
        description_doc = jira_data.get("description", {})
        
        description = ""
        if description_doc:
            description = self.convert_jira_doc_to_markdown(description_doc)
        
        tr = execution.get("testRuns")
        test_runs = tr.get("results", []) if isinstance(tr, dict) else []
        
        # Collect case IDs for this run
        case_ids = []
        for test_run in test_runs:
            if not isinstance(test_run, dict):
                continue
            test_ref = test_run.get("test")
            test_ref = test_ref if isinstance(test_ref, dict) else {}
            test_issue_id = test_ref.get("issueId", "")
            if test_issue_id:
                # Case ID will be resolved during load phase
                case_ids.append(test_issue_id)
        
        if not case_ids:
            return None
        
        # Create run
        qase_run = {
            "title": summary,
            "description": description,
            "cases": case_ids,
            "start_time": None,  # Will be set from test runs
            "end_time": None,
            "_execution_issue_id": execution.get("issueId"),
            "_project_code": project_code
        }
        
        return qase_run
    
    def _transform_test_run_to_result(
        self,
        test_run: Dict[str, Any],
        execution: Dict[str, Any],
        project_code: str,
        migrated_test_ids: Set[str],
    ) -> Optional[Dict[str, Any]]:
        """
        Transform a single Xray test run to Qase result.
        
        Args:
            test_run: Xray test run data
            execution: Parent execution data
            project_code: Qase project code
            migrated_test_ids: Xray test issue ids present in transformed cases (strings)
        
        Returns:
            Qase result payload or None if transformation fails
        """
        if not isinstance(test_run, dict):
            return None
        test_ref = test_run.get("test")
        test_ref = test_ref if isinstance(test_ref, dict) else {}
        test_issue_id = test_ref.get("issueId", "")
        sid = str(test_issue_id) if test_issue_id is not None else ""
        if not sid or sid not in migrated_test_ids:
            return None
        
        st_run = test_run.get("status")
        st_run = st_run if isinstance(st_run, dict) else {}
        status = (st_run.get("name", "") or "").lower()
        qase_status = self._map_xray_status_to_qase(status)
        
        started_on = test_run.get("startedOn")
        finished_on = test_run.get("finishedOn")
        
        # Convert timestamps
        start_time = None
        end_time = None
        duration = 0
        
        if started_on:
            try:
                start_time = int(datetime.fromisoformat(started_on.replace("Z", "+00:00")).timestamp())
            except:
                pass
        
        if finished_on:
            try:
                end_time = int(datetime.fromisoformat(finished_on.replace("Z", "+00:00")).timestamp())
            except:
                pass
        
        if start_time and end_time:
            duration = (end_time - start_time) * 1000  # Convert to milliseconds
        elif start_time:
            duration = 5000  # Default 5 seconds if no end time
            end_time = start_time + 5
        
        run_comment = self._format_xray_text_field(test_run.get("comment"))
        defect_lines_run = self._defect_lines(test_run.get("defects"))
        run_evidence_ids = self._evidence_attachment_ids(test_run.get("evidence"))
        run_evidence_md = self._media_caption_lines(test_run.get("evidence"))

        message_parts: List[str] = []
        if run_comment:
            message_parts.append(run_comment)
        if defect_lines_run:
            message_parts.append("**Defects**\n" + "\n".join(defect_lines_run))
        if run_evidence_md:
            message_parts.append("**Evidence**\n" + "\n".join(run_evidence_md))
        message = "\n\n".join(message_parts)

        all_result_attachment_ids: List[str] = list(run_evidence_ids)

        # Process step results (actual results, comments, defects, evidence → Qase step comment + attachments)
        step_results = []
        steps = test_run.get("steps", [])
        
        for step in steps:
            if not isinstance(step, dict):
                continue
            st = step.get("status")
            st = st if isinstance(st, dict) else {}
            step_status = st.get("name", "").lower()
            qase_step_status = self._map_xray_step_status_to_qase(step_status)

            step_evidence_ids = self._evidence_attachment_ids(step.get("evidence"))
            step_attach_ids: List[str] = []
            for att in step.get("attachments") or []:
                if isinstance(att, dict) and att.get("id"):
                    step_attach_ids.append(str(att["id"]))
            step_xray_ids = list(
                dict.fromkeys(step_evidence_ids + step_attach_ids)
            )
            all_result_attachment_ids.extend(step_xray_ids)

            actual_md = self._format_xray_text_field(step.get("actualResult"))
            comm_md = self._format_xray_text_field(step.get("comment"))
            step_defect_lines = self._defect_lines(step.get("defects"))
            media_lines = self._media_caption_lines(step.get("evidence"))
            for att in step.get("attachments") or []:
                if isinstance(att, dict):
                    fn = (att.get("filename") or "attachment").strip()
                    dl = att.get("downloadLink")
                    if dl:
                        media_lines.append(f"- [{fn}]({dl})")
                    elif fn:
                        media_lines.append(f"- {fn}")

            step_body: List[str] = []
            if actual_md:
                step_body.append(f"**Actual result**\n{actual_md}")
            if comm_md:
                step_body.append(f"**Comment**\n{comm_md}")
            if step_defect_lines:
                step_body.append("**Defects**\n" + "\n".join(step_defect_lines))
            if media_lines:
                step_body.append("**Evidence & attachments**\n" + "\n".join(media_lines))
            step_comment = "\n\n".join(step_body) if step_body else ""

            action_txt = self._format_xray_text_field(step.get("action")) or "."
            expected_txt = self._format_xray_text_field(step.get("result"))

            step_result = {
                "data": {
                    "action": action_txt,
                    "expected_result": expected_txt,
                },
                "execution": {
                    "status": qase_step_status,
                    "comment": step_comment or None,
                },
                "_xray_attachment_ids": step_xray_ids,
            }
            step_results.append(step_result)
        
        ej = execution.get("jira") or {}
        execution_summary = ej.get("summary", "") or ""
        title = (execution_summary or "").strip() or f"Test run result ({sid})"
        
        qase_result = {
            "title": title,
            # Xray test issue id; loader maps this to Qase case id via MappingStore
            "testops_id": sid,
            "_execution_issue_id": execution.get("issueId"),
            "_xray_attachment_ids": list(dict.fromkeys(all_result_attachment_ids)),
            "execution": {
                "status": qase_status,
                "duration": duration,
                "start_time": start_time or int(datetime.now().timestamp()),
                "end_time": end_time or (start_time or int(datetime.now().timestamp())) + 5
            },
            "message": message,
            "attachments": [],
            "steps": step_results if step_results else None
        }
        
        return qase_result

    def _format_xray_text_field(self, value: Any) -> str:
        """Xray/Jira fields may be plain string or ADF (dict)."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            if not value:
                return ""
            return self.convert_jira_doc_to_markdown(value).strip()
        return str(value).strip()

    def _defect_lines(self, defects: Any) -> List[str]:
        """Format Xray defects (issue keys/ids) as markdown bullet lines."""
        if not defects:
            return []
        lines: List[str] = []
        if isinstance(defects, list):
            for d in defects:
                if isinstance(d, str) and d.strip():
                    lines.append(f"- {d.strip()}")
                elif isinstance(d, dict):
                    key = d.get("key") or d.get("issueKey")
                    jid = d.get("issueId") or d.get("id")
                    if key:
                        lines.append(f"- {key}")
                    elif jid is not None:
                        lines.append(f"- Jira issue id `{jid}`")
        elif isinstance(defects, str) and defects.strip():
            lines.append(f"- {defects.strip()}")
        return lines

    def _evidence_attachment_ids(self, items: Any) -> List[str]:
        ids: List[str] = []
        for ev in items or []:
            if isinstance(ev, dict) and ev.get("id"):
                ids.append(str(ev["id"]))
        return ids

    def _media_caption_lines(self, items: Any) -> List[str]:
        """Human-readable evidence lines (links work only while Jira session/token valid)."""
        lines: List[str] = []
        for ev in items or []:
            if not isinstance(ev, dict):
                continue
            fn = (ev.get("filename") or "file").strip()
            dl = ev.get("downloadLink")
            if dl:
                lines.append(f"- [{fn}]({dl})")
            elif fn:
                lines.append(f"- {fn}")
        return lines
    
    def _map_xray_status_to_qase(self, xray_status: str) -> str:
        """
        Map Xray test-run status to Qase result execution status (v2 allows custom strings).
        
        Xray TODO / not-yet-run → ``untested`` (not ``skipped``). Loader leaves the run open
        when any result is untested.
        """
        status_lower = (xray_status or "").lower()
        
        if status_lower in ("passed", "pass", "ok", "success", "successful"):
            return "passed"
        if status_lower in ("failed", "fail", "nok", "error", "erroneous"):
            return "failed"
        if status_lower in ("blocked", "block", "aborted", "cancelled", "canceled"):
            return "blocked"
        if status_lower in (
            "todo",
            "to do",
            "to_do",
            "not executed",
            "not_executed",
            "untested",
            "not run",
            "not_run",
            "n/a",
            "na",
        ):
            return "untested"
        if status_lower in ("skip", "skipped"):
            return "skipped"
        return "skipped"

    def _map_xray_step_status_to_qase(self, xray_status: str) -> str:
        """
        Map Xray step status to values accepted by Qase ``ResultStepStatus`` (no ``untested``).
        TODO-like steps map to ``in_progress``.
        """
        status_lower = (xray_status or "").lower()
        if status_lower in ("passed", "pass", "ok", "success", "successful"):
            return "passed"
        if status_lower in ("failed", "fail", "nok", "error", "erroneous"):
            return "failed"
        if status_lower in ("blocked", "block", "aborted", "cancelled", "canceled"):
            return "blocked"
        if status_lower in (
            "todo",
            "to do",
            "to_do",
            "not executed",
            "not_executed",
            "untested",
            "not run",
            "not_run",
            "n/a",
            "na",
            "executing",
            "in progress",
            "in_progress",
        ):
            return "in_progress"
        if status_lower in ("skip", "skipped"):
            return "skipped"
        return "skipped"
