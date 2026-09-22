"""Transformer for Xray test cases to Qase cases."""

from collections import Counter
from typing import Dict, Any, List, Optional, Set, Tuple
from utils.cache_manager import CacheManager
from utils.logger import get_logger
from models.mappings import MappingStore
from transformers.xray_transformer import BaseTransformer

logger = get_logger(__name__)


def _jira_priority_display_name(jira_data: Dict[str, Any]) -> Optional[str]:
    p = jira_data.get("priority")
    if isinstance(p, dict):
        for key in ("name", "displayName", "label"):
            v = p.get(key)
            if isinstance(v, str) and v.strip():
                return v.strip()
    if isinstance(p, str) and p.strip():
        return p.strip()
    return None


class CaseTransformer(BaseTransformer):
    """Transforms Xray test cases to Qase cases."""
    
    def _project_code_for_case(self, test_case: Dict[str, Any]) -> Optional[str]:
        """Resolve Qase project code from Xray test (projectId and/or embedded jira.project)."""
        pid = test_case.get("projectId")
        if pid is not None and str(pid).strip() != "":
            code = self.mappings.get_qase_id(str(pid), "project")
            if code:
                return code
        jp = (test_case.get("jira") or {}).get("project")
        if isinstance(jp, dict):
            jid = jp.get("id")
            if jid is not None and str(jid).strip() != "":
                code = self.mappings.get_qase_id(str(jid), "project")
                if code:
                    return code
            jk = jp.get("key")
            if jk:
                code = self.mappings.get_qase_id(str(jk).strip().upper(), "project")
                if code:
                    return code
        return None
    
    def transform(
        self,
        test_cases_data: List[Dict[str, Any]],
        qase_suites: Dict[str, List[Dict[str, Any]]],
        attachments_map: Dict[str, Dict[str, str]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Transform Xray test cases to Qase cases.
        
        Args:
            test_cases_data: List of Xray test case data
            qase_suites: Dictionary mapping project_code → list of suites
            attachments_map: Map of Xray attachment ID → Qase attachment object
        
        Returns:
            Dictionary mapping project_code → list of case payloads
        """
        pair_counts: Counter = Counter()
        for tc in test_cases_data:
            pc = self._project_code_for_case(tc)
            if not pc:
                continue
            j = tc.get("jira") or {}
            raw_summary = (j.get("summary") or "").strip()
            pair_counts[(pc, raw_summary)] += 1
        duplicate_title_pairs: Set[Tuple[str, str]] = {
            k for k, c in pair_counts.items() if c > 1
        }
        if duplicate_title_pairs:
            self.logger.info(
                "Disambiguating %s duplicate (project, title) pair(s) for Qase import",
                len(duplicate_title_pairs),
            )
        
        # Group cases by project
        project_cases: Dict[str, List[Dict[str, Any]]] = {}
        
        for test_case in test_cases_data:
            try:
                project_id = str(test_case.get("projectId", ""))
                
                project_code = self._project_code_for_case(test_case)
                if not project_code:
                    available = list(self.mappings.mappings.keys())
                    self.logger.warning(
                        f"No project mapping for test issue {test_case.get('issueId')} "
                        f"(projectId={project_id!r}). "
                        f"Mapping keys (sample): {available[:20]}{'...' if len(available) > 20 else ''}. Skipping case."
                    )
                    continue
                
                if project_code not in project_cases:
                    project_cases[project_code] = []
                
                # Transform test case
                qase_case = self._transform_single_case(
                    test_case,
                    qase_suites.get(project_code, []),
                    attachments_map,
                    project_code,
                    duplicate_title_pairs,
                )
                
                if qase_case:
                    project_cases[project_code].append(qase_case)
                
            except Exception as e:
                self.logger.error(f"Error transforming test case {test_case.get('issueId')}: {e}")
                continue
        
        total_cases = sum(len(cases) for cases in project_cases.values())
        self.logger.info(f"Transformed {total_cases} test cases across {len(project_cases)} projects")
        
        return project_cases
    
    def _transform_single_case(
        self,
        test_case: Dict[str, Any],
        suites: List[Dict[str, Any]],
        attachments_map: Dict[str, Dict[str, str]],
        project_code: str,
        duplicate_title_pairs: Set[Tuple[str, str]],
    ) -> Optional[Dict[str, Any]]:
        """
        Transform a single Xray test case to Qase case format.
        
        Args:
            test_case: Xray test case data
            suites: List of suites for the project
            attachments_map: Map of attachment IDs
        
        Returns:
            Qase case payload or None if transformation fails
        """
        jira_data = test_case.get("jira", {}) or {}
        summary = jira_data.get("summary", "") or ""
        raw_summary = summary.strip()
        description_doc = jira_data.get("description", {})
        issue_id = test_case.get("issueId")
        jira_key = jira_data.get("key") or ""
        
        # Convert description to markdown
        description = ""
        if description_doc:
            description = self.convert_jira_doc_to_markdown(description_doc)
        
        # Process steps (Qase requires non-empty step action; omit steps that are entirely empty)
        steps = []
        xray_steps = test_case.get("steps") or []
        if not isinstance(xray_steps, list):
            xray_steps = []
        case_attachments = []
        
        def _step_field_as_text(field: Any) -> str:
            if field is None:
                return ""
            if isinstance(field, dict):
                return self.convert_jira_doc_to_markdown(field)
            if isinstance(field, str):
                return field
            return str(field)
        
        for step in xray_steps:
            if not isinstance(step, dict):
                continue
            action = _step_field_as_text(step.get("action"))
            result = _step_field_as_text(step.get("result"))
            data = step.get("data", "")
            if not isinstance(data, str):
                data = str(data) if data is not None else ""
            
            action_processed, action_attachments = self.replace_attachment_references(
                action, attachments_map
            )
            result_processed, result_attachments = self.replace_attachment_references(
                result, attachments_map
            )
            
            case_attachments.extend(action_attachments)
            case_attachments.extend(result_attachments)
            
            action_clean = (action_processed or "").strip()
            result_clean = (result_processed or "").strip()
            data_clean = (data or "").strip()
            
            if not action_clean and not result_clean and not data_clean:
                continue
            
            if not action_clean:
                action_clean = "."
            
            qase_step = {
                "action": action_clean,
                "expected_result": result_clean,
                "data": data_clean,
                "position": len(steps) + 1,
            }
            steps.append(qase_step)
        
        # Process case-level attachments
        # Store Xray attachment IDs - will be resolved to hashes after upload
        xray_attachment_ids = []
        jira_attachments = jira_data.get("attachment", [])
        for att_ref in jira_attachments:
            if isinstance(att_ref, str):
                att_id = att_ref
                xray_attachment_ids.append(att_id)
                # If hash already available, add it
                att_data = attachments_map.get(att_id)
                if att_data and att_data.get("hash"):
                    case_attachments.append(att_data["hash"])
            elif isinstance(att_ref, dict):
                att_id = str(att_ref.get("id", att_ref.get("attachmentId", "")))
                xray_attachment_ids.append(att_id)
                # If hash already available, add it
                att_data = attachments_map.get(att_id)
                if att_data and att_data.get("hash"):
                    case_attachments.append(att_data["hash"])
        
        # Process attachments in description
        desc_processed, desc_attachments = self.replace_attachment_references(
            description, attachments_map
        )
        description = desc_processed
        case_attachments.extend(desc_attachments)
        
        # Remove duplicate attachments
        case_attachments = list(set(case_attachments))
        
        # Find suite ID (match by folder path)
        folder_path = test_case.get("folder", {}).get("path", "")
        suite_id = None
        
        if folder_path and suites:
            # Find matching suite by path
            for suite in suites:
                suite_desc = suite.get("description", "")
                if folder_path in suite_desc:
                    # Suite ID will be set after creation, for now we track path
                    suite_id = suite.get("_suite_id")
                    break
        
        # Map test type
        test_type_obj = test_case.get("testType") or {}
        test_type = (test_type_obj.get("name", "Manual") or "Manual") if isinstance(test_type_obj, dict) else "Manual"
        automation = 0  # Manual by default
        if test_type.lower() in ["automated", "automation", "cucumber", "gherkin"]:
            automation = 1

        jira_priority_name = _jira_priority_display_name(jira_data)

        # Extract labels
        labels = jira_data.get("labels", [])
        if isinstance(labels, str):
            labels = [l.strip() for l in labels.split(",")]
        if isinstance(labels, list):
            labels = [str(l).strip() for l in labels if l is not None and str(l).strip()]
        
        title = raw_summary
        if len(title) > 255:
            title = title[:252].rstrip() + "..."
        if not title:
            title = (jira_key or f"Xray test {issue_id}" or "Untitled test case").strip()[:255]
        if (project_code, raw_summary) in duplicate_title_pairs:
            tag = (jira_key or str(issue_id) or "").strip()
            if tag:
                suf = f" ({tag})"
                if len(title) + len(suf) > 255:
                    title = title[: max(0, 255 - len(suf))].rstrip() + suf
                else:
                    title = title + suf
        
        qase_case = {
            "title": title,
            "description": description,
            "preconditions": "",
            "postconditions": "",
            "severity": 2,  # Normal (default)
            "type": 1,      # Functional (default)
            "behavior": 1,   # Positive (default)
            "automation": automation,
            "status": 1,     # Actual (default)
            "suite_id": suite_id,  # Will be set after suite creation
            "steps": steps,
            "attachments": case_attachments,  # Hashes if available, will be updated after upload
            "tags": labels,  # Qase uses tags for labels
            "_xray_issue_id": test_case.get("issueId"),  # Store for reference / mappings
            "_jira_issue_key": (jira_key or "").strip(),  # e.g. XSP-50 — used for preserve_xray_case_ids → Qase id
            "_jira_priority_name": jira_priority_name,
            "_folder_path": folder_path,  # Store for suite matching
            "_xray_attachment_ids": list(set(xray_attachment_ids))  # Store Xray IDs for later resolution
        }
        
        return qase_case
