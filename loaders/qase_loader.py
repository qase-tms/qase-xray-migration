"""Loader for importing transformed data into Qase."""

import copy
import json
from typing import Dict, Any, List, Optional
from pathlib import Path
from tqdm import tqdm

from services.qase_service import QaseService
from utils.cache_manager import CacheManager
from utils.logger import get_logger
from models.mappings import MappingStore
from transformers.xray_transformer import (
    replace_jira_attachment_refs_in_text,
    replace_xray_cloud_attachment_urls_in_text,
)

logger = get_logger(__name__)


def _jira_key_issue_number(key: str) -> Optional[int]:
    """Trailing number from a Jira issue key (e.g. ``XSP-50`` → ``50``). Not the internal issue id."""
    if not key or not isinstance(key, str):
        return None
    s = key.strip()
    if "-" not in s:
        return None
    suffix = s.rsplit("-", 1)[-1].strip()
    if suffix.isdigit():
        return int(suffix)
    return None


class QaseLoader:
    """
    Loads transformed data into Qase.
    
    Loads in order:
    1. Projects
    2. Attachments (upload and update mappings)
    3. Suites (hierarchical)
    4. Cases (bulk)
    5. Runs
    6. Results (bulk)
    """
    
    def __init__(
        self,
        cache_manager: CacheManager,
        qase_service: QaseService,
        mappings: MappingStore,
        preserve_xray_case_ids: bool = False,
    ):
        """
        Initialize loader.
        
        Args:
            cache_manager: CacheManager instance for reading transformed data
            qase_service: QaseService instance for API calls
            mappings: MappingStore for tracking ID mappings
            preserve_xray_case_ids: If True, bulk-create cases with Qase id from Jira issue key number (fallback: internal issue id)
        """
        self.cache_manager = cache_manager
        self.qase_service = qase_service
        self.mappings = mappings
        self.preserve_xray_case_ids = preserve_xray_case_ids
        
        self.stats = {
            "projects": 0,
            "attachments": 0,
            "suites": 0,
            "cases": 0,
            "runs": 0,
            "results": 0,
            "errors": 0
        }
    
    def load(self) -> Dict[str, Any]:
        """
        Load all transformed data into Qase.
        
        Returns:
            Dictionary with loading statistics
        """
        logger.info("Starting Qase data import...")
        if self.preserve_xray_case_ids:
            logger.info(
                "preserve_xray_case_ids is enabled: Qase case id = number from Jira issue key "
                "(e.g. XSP-50 → 50) so cards match Jira; falls back to internal issue id if key is missing"
            )

        try:
            # Load transformed data
            transformed_dir = self.cache_manager.cache_dir / "transformed"
            if not transformed_dir.exists():
                raise ValueError(f"Transformed data directory not found: {transformed_dir}")
            
            projects = self._load_json(transformed_dir / "projects.json")
            suites = self._load_json(transformed_dir / "suites.json")
            cases = self._load_json(transformed_dir / "cases.json")
            attachments_map = self._load_json(transformed_dir / "attachments_map.json")
            runs = self._load_json(transformed_dir / "runs.json") or []
            results = self._load_json(transformed_dir / "results.json") or []
            
            # Load in order
            logger.info("Step 1: Creating projects...")
            self._load_projects(projects)
            
            logger.info("Step 2: Uploading attachments...")
            self._load_attachments(attachments_map)
            
            logger.info("Step 3: Updating cases with attachment hashes...")
            self._update_cases_with_attachment_hashes(cases, attachments_map)
            
            logger.info("Step 4: Creating suites...")
            suite_maps = self._load_suites(suites)
            
            logger.info("Step 5: Creating cases...")
            self._load_cases(cases, suite_maps)
            
            if runs:
                logger.info("Step 6: Creating runs and results...")
                self._load_runs_and_results(runs, results, attachments_map or {})
            
            # Save updated mappings
            self._save_mappings()
            
            logger.info("Data import complete!")
            return {
                "status": "success",
                "stats": self.stats
            }
            
        except Exception as e:
            logger.error(f"Data import failed: {e}", exc_info=True)
            self.stats["errors"] += 1
            return {
                "status": "error",
                "error": str(e),
                "stats": self.stats
            }
    
    def _load_json(self, file_path: Path) -> Any:
        """Load JSON file."""
        if not file_path.exists():
            return None
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    
    def _load_projects(self, projects: List[Dict[str, Any]]):
        """Load projects into Qase."""
        for project in tqdm(projects, desc="Creating projects"):
            try:
                result = self.qase_service.create_project(project)
                project_code = project.get("code")
                
                # Update mapping with Qase project code (should match, but verify)
                # Find the Xray project ID from mappings
                for xray_id, mapping in self.mappings.mappings.items():
                    if mapping.entity_type == "project" and mapping.qase_id == project_code:
                        # Mapping already exists, just verify
                        logger.debug(f"Project {project_code} created successfully")
                        break
                
                self.stats["projects"] += 1
                
            except Exception as e:
                logger.error(f"Error creating project {project.get('code')}: {e}")
                self.stats["errors"] += 1
    
    def _load_attachments(self, attachments_map: Dict[str, Dict[str, str]]):
        """Upload attachments and update mappings."""
        if not attachments_map:
            return
        
        # Group attachments by project (we need project code for upload)
        # For now, we'll need to determine project from mappings or use first project
        # This is a limitation - we need project code to upload attachments
        
        # Get first project code from mappings
        project_code = None
        for mapping in self.mappings.mappings.values():
            if mapping.entity_type == "project":
                project_code = mapping.qase_id
                break
        
        if not project_code:
            logger.warning("No project code found, skipping attachment upload")
            return
        
        for xray_id, att_data in tqdm(attachments_map.items(), desc="Uploading attachments"):
            try:
                local_path = att_data.get("local_path")
                if not local_path:
                    logger.warning(f"Attachment {xray_id} has no local_path, skipping")
                    continue
                
                # Handle different path formats
                # local_path might be "attachments\filename.png" or just "filename.png"
                if "\\" in local_path or "/" in local_path:
                    # Path contains directory separator
                    if local_path.startswith("attachments"):
                        # Relative path like "attachments\filename.png"
                        file_path = self.cache_manager.cache_dir / local_path.replace("\\", "/")
                    else:
                        # Absolute or relative path
                        file_path = Path(local_path)
                else:
                    # Just filename, look in attachments directory
                    file_path = self.cache_manager.cache_dir / "attachments" / local_path
                
                if not file_path.exists():
                    logger.warning(f"Attachment file not found: {file_path}")
                    continue
                
                logger.debug(f"Uploading attachment {xray_id}: {file_path} (exists: {file_path.exists()}, size: {file_path.stat().st_size} bytes)")
                result = self.qase_service.upload_attachment(project_code, file_path)
                
                # Update attachment mapping with Qase hash and URL
                att_data["hash"] = result.get("hash")
                att_data["url"] = result.get("url")
                
                self.stats["attachments"] += 1
                
            except Exception as e:
                logger.error(f"Error uploading attachment {xray_id}: {e}")
                self.stats["errors"] += 1
    
    def _update_cases_with_attachment_hashes(
        self,
        cases: Dict[str, List[Dict[str, Any]]],
        attachments_map: Dict[str, Dict[str, str]]
    ):
        """
        Update cases with attachment hashes after attachments are uploaded.
        
        Cases were transformed before attachments were uploaded, so they reference
        attachments by Xray ID. We need to replace those with Qase attachment hashes.
        """
        logger.info(f"Updating cases with attachment hashes. Attachments map has {len(attachments_map)} entries")
        logger.debug(f"Attachment map keys: {list(attachments_map.keys())[:5]}...")  # Show first 5 keys
        
        total_attachments_added = 0
        for project_code, case_list in cases.items():
            for case in case_list:
                updated_attachments = []
                case_title = case.get("title", "Unknown")
                
                # Start with any existing hashes (from inline attachments in description)
                current_attachments = case.get("attachments", [])
                for att_ref in current_attachments:
                    if isinstance(att_ref, str):
                        # Check if it's already a hash (long alphanumeric string)
                        if len(att_ref) > 20 and all(c.isalnum() or c in '-_' for c in att_ref):
                            # Looks like a hash, keep it
                            updated_attachments.append(att_ref)
                
                # Resolve Xray attachment IDs to Qase hashes
                xray_attachment_ids = case.get("_xray_attachment_ids", [])
                
                # Fallback: if _xray_attachment_ids is missing (old transformed data),
                # load from raw test case data
                if not xray_attachment_ids:
                    xray_issue_id = case.get("_xray_issue_id")
                    if xray_issue_id:
                        # Load raw test case to get attachment IDs
                        try:
                            raw_cases = self._load_json(
                                self.cache_manager.raw_data_dir / "test_cases.json"
                            )
                            if raw_cases:
                                for raw_case in raw_cases:
                                    raw_jira = raw_case.get("jira", {})
                                    raw_issue_id = str(raw_jira.get("id", ""))
                                    if raw_issue_id == str(xray_issue_id):
                                        # Found matching case, extract attachment IDs
                                        raw_attachments = raw_jira.get("attachment", [])
                                        for att_ref in raw_attachments:
                                            if isinstance(att_ref, str):
                                                xray_attachment_ids.append(att_ref)
                                            elif isinstance(att_ref, dict):
                                                att_id = att_ref.get("id") or att_ref.get("attachmentId")
                                                if att_id:
                                                    xray_attachment_ids.append(str(att_id))
                                        break
                        except Exception as e:
                            logger.debug(f"Could not load raw test case for {xray_issue_id}: {e}")
                
                if xray_attachment_ids:
                    logger.debug(f"Case '{case_title}' has {len(xray_attachment_ids)} Xray attachment IDs: {xray_attachment_ids[:3]}...")
                
                for xray_id in xray_attachment_ids:
                    # Try multiple key formats
                    att_data = None
                    xray_id_str = str(xray_id)
                    
                    # Try exact match first
                    att_data = attachments_map.get(xray_id_str)
                    
                    # If not found, try other formats
                    if not att_data:
                        # Try without string conversion
                        att_data = attachments_map.get(xray_id)
                    
                    if not att_data:
                        # Try with different prefixes/suffixes
                        for prefix in ["", "attachment_", "att_"]:
                            for suffix in ["", "_attachment"]:
                                test_key = f"{prefix}{xray_id_str}{suffix}"
                                att_data = attachments_map.get(test_key)
                                if att_data:
                                    break
                            if att_data:
                                break
                    
                    if att_data and att_data.get("hash"):
                        updated_attachments.append(att_data["hash"])
                        total_attachments_added += 1
                        logger.debug(f"Resolved attachment {xray_id} -> hash {att_data['hash'][:10]}...")
                    else:
                        logger.warning(f"Attachment {xray_id} not found in map for case '{case_title}'. Available keys: {list(attachments_map.keys())[:10]}")
                
                # Jira wiki !file.png|...! and [^file] in description/steps → markdown with Qase CDN URLs
                inline_hashes: List[str] = []
                new_desc, hd = replace_jira_attachment_refs_in_text(
                    case.get("description") or "", attachments_map
                )
                new_desc, hx = replace_xray_cloud_attachment_urls_in_text(new_desc, attachments_map)
                case["description"] = new_desc
                inline_hashes.extend(hd)
                inline_hashes.extend(hx)
                for step in case.get("steps") or []:
                    if not isinstance(step, dict):
                        continue
                    for key in ("action", "expected_result", "data"):
                        val = step.get(key)
                        if isinstance(val, str) and val.strip():
                            nv, hv = replace_jira_attachment_refs_in_text(val, attachments_map)
                            nv, hx2 = replace_xray_cloud_attachment_urls_in_text(nv, attachments_map)
                            step[key] = nv
                            inline_hashes.extend(hv)
                            inline_hashes.extend(hx2)

                merged = list(dict.fromkeys(updated_attachments + inline_hashes))
                if merged:
                    case["attachments"] = merged
                    logger.debug(f"Case '{case_title}' now has {len(case['attachments'])} attachments")
                else:
                    case["attachments"] = []
        
        logger.info(f"Added {total_attachments_added} attachment hashes to cases")
    
    def _load_suites(self, suites: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, int]]:
        """
        Load suites into Qase (hierarchical).
        
        Returns:
            Dictionary mapping project_code → {folder_path → suite_id}
        """
        suite_maps: Dict[str, Dict[str, int]] = {}
        
        for project_code, suite_list in suites.items():
            # Build parent mapping as we create suites
            path_to_suite_id: Dict[str, int] = {}
            suite_maps[project_code] = path_to_suite_id
            
            for suite in tqdm(suite_list, desc=f"Creating suites for {project_code}"):
                try:
                    # Handle parent relationship
                    suite_data = suite.copy()
                    parent_path = suite_data.pop("_parent_path", None)
                    if parent_path and parent_path in path_to_suite_id:
                        suite_data["parent_id"] = path_to_suite_id[parent_path]
                    else:
                        suite_data["parent_id"] = None
                    
                    result = self.qase_service.create_suite(project_code, suite_data)
                    suite_id = result.get("id")
                    
                    # Store suite ID for parent relationships and case matching
                    suite_desc = suite.get("description", "")
                    # Extract path from description: "Migrated from Xray folder: {path}"
                    if "Migrated from Xray folder:" in suite_desc:
                        path = suite_desc.split("Migrated from Xray folder:")[-1].strip()
                        path_to_suite_id[path] = suite_id
                    
                    self.stats["suites"] += 1
                    
                except Exception as e:
                    logger.error(f"Error creating suite {suite.get('title')}: {e}")
                    self.stats["errors"] += 1
        
        return suite_maps
    
    def _xray_issue_id_to_jira_key_map(self) -> Dict[str, str]:
        """Map Xray/Jira test issueId → Jira issue key from raw extract (for older transformed caches)."""
        raw = self._load_json(self.cache_manager.raw_data_dir / "test_cases.json")
        out: Dict[str, str] = {}
        for tc in raw or []:
            if not isinstance(tc, dict):
                continue
            iid = tc.get("issueId")
            if iid is None:
                continue
            jira = tc.get("jira") or {}
            k = jira.get("key")
            if k:
                out[str(iid).strip()] = str(k).strip()
        return out

    def _load_cases(self, cases: Dict[str, List[Dict[str, Any]]], suite_maps: Dict[str, Dict[str, int]]):
        """Load cases into Qase (bulk)."""
        self.qase_service.load_system_fields()

        issue_id_to_key: Dict[str, str] = {}
        if self.preserve_xray_case_ids:
            issue_id_to_key = self._xray_issue_id_to_jira_key_map()

        for project_code, case_list in cases.items():
            # Update suite IDs in cases using the suite map from loaded suites
            suite_map = suite_maps.get(project_code, {})
            for case in case_list:
                folder_path = case.get("_folder_path")
                if folder_path and folder_path in suite_map:
                    case["suite_id"] = suite_map[folder_path]
                elif folder_path:
                    logger.debug(f"Case {case.get('title')} has folder path {folder_path} but no matching suite found")

            for case in case_list:
                pname = case.pop("_jira_priority_name", None)
                if pname:
                    qpid = self.qase_service.resolve_priority_id_by_jira_name(pname)
                    if qpid is not None:
                        case["priority"] = qpid
                    else:
                        case.pop("priority", None)
                else:
                    # Drop any stale numeric priority from old transformed caches (e.g. mistaken 1 = "low")
                    case.pop("priority", None)

            # Process in batches of 100 (Qase limit)
            batch_size = 100
            for i in tqdm(range(0, len(case_list), batch_size), desc=f"Creating cases for {project_code}"):
                batch = case_list[i:i + batch_size]
                
                # Clean up internal fields before sending
                clean_batch = []
                for case in batch:
                    clean_case = {k: v for k, v in case.items() if not k.startswith("_")}
                    if self.preserve_xray_case_ids:
                        jira_key = (case.get("_jira_issue_key") or "").strip()
                        if not jira_key:
                            xlookup = case.get("_xray_issue_id")
                            if xlookup is not None:
                                jira_key = issue_id_to_key.get(str(xlookup).strip(), "")
                        qid = _jira_key_issue_number(jira_key) if jira_key else None
                        if qid is not None:
                            clean_case["id"] = qid
                        else:
                            xid = case.get("_xray_issue_id")
                            if xid is not None:
                                xs = str(xid).strip()
                                if xs.isdigit():
                                    clean_case["id"] = int(xs)
                                    logger.debug(
                                        "preserve_xray_case_ids: no parsable Jira key for case %r; "
                                        "using internal issue id %s",
                                        case.get("title"),
                                        xs,
                                    )
                                else:
                                    logger.warning(
                                        "preserve_xray_case_ids: cannot derive id (key=%r, issueId=%r) for %r",
                                        jira_key or None,
                                        xid,
                                        case.get("title"),
                                    )
                    clean_batch.append(clean_case)
                
                try:
                    result = self.qase_service.create_cases_bulk(project_code, clean_batch)
                    
                    created_cases = result.get("cases", [])
                    created_ids = result.get("ids") or []
                    created_count = result.get("created_count")
                    if created_count is None:
                        created_count = len(created_ids) if created_ids else len(created_cases)
                    
                    if created_count != len(clean_batch):
                        logger.error(
                            "Project %s: Qase created %s of %s cases in this batch. "
                            "Check for duplicate titles in Qase or API limits. "
                            "Re-transform after fixing titles if needed.",
                            project_code,
                            created_count,
                            len(clean_batch),
                        )
                    
                    self.stats["cases"] += created_count
                    
                    id_list = created_ids if created_ids else [c.get("id") for c in created_cases]
                    for j, qase_case_id in enumerate(id_list):
                        if j >= len(batch):
                            break
                        if qase_case_id is None:
                            continue
                        original_case = batch[j]
                        xray_issue_id = original_case.get("_xray_issue_id")
                        if xray_issue_id:
                            self.mappings.add_mapping(
                                xray_id=str(xray_issue_id),
                                qase_id=str(qase_case_id),
                                entity_type="case",
                            )
                    
                except Exception as e:
                    logger.error(f"Error creating cases batch: {e}")
                    self.stats["errors"] += 1
    
    def _load_runs_and_results(
        self,
        runs: List[Dict[str, Any]],
        results: List[Dict[str, Any]],
        attachments_map: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        """Load runs and results into Qase."""
        attachments_map = attachments_map or {}
        for run_data in tqdm(runs, desc="Creating runs"):
            try:
                project_code = run_data.get("_project_code")
                if not project_code:
                    continue
                
                # Resolve case IDs from Xray issue IDs
                case_ids = []
                for xray_case_id in run_data.get("cases", []):
                    qase_case_id = self.mappings.get_qase_id(str(xray_case_id), "case")
                    if qase_case_id:
                        case_ids.append(int(qase_case_id))
                
                if not case_ids:
                    logger.warning(f"Run {run_data.get('title')} has no valid cases, skipping")
                    continue
                
                raw_desc = run_data.get("description", "") or ""
                run_desc, _ = replace_jira_attachment_refs_in_text(
                    raw_desc, attachments_map
                )
                run_desc, _ = replace_xray_cloud_attachment_urls_in_text(
                    run_desc, attachments_map
                )
                run_payload = {
                    "title": run_data.get("title"),
                    "description": run_desc,
                    "cases": case_ids
                }
                
                result = self.qase_service.create_run(project_code, run_payload)
                run_id = result.get("id")
                if run_id is None:
                    logger.error("Create run returned no id for %r", run_data.get("title"))
                    self.stats["errors"] += 1
                    continue
                run_id = int(run_id)
                
                # Match results to this run: testops_id is Xray test issue id; scope by execution when present
                run_execution_id = run_data.get("_execution_issue_id")
                run_results = []
                for r in results:
                    rex = r.get("_execution_issue_id")
                    if (
                        rex is not None
                        and run_execution_id is not None
                        and str(rex) != str(run_execution_id)
                    ):
                        continue
                    xray_case_id = str(r.get("testops_id", ""))
                    qase_case_id = self.mappings.get_qase_id(xray_case_id, "case")
                    if qase_case_id and int(qase_case_id) in case_ids:
                        run_results.append(r)
                
                if not run_results and results:
                    logger.warning(
                        "Run %r (id=%s): no results matched %s case id(s) in run — "
                        "check transformed results use Xray test issue ids as testops_id; re-run transform.",
                        run_data.get("title"),
                        run_id,
                        len(case_ids),
                    )
                
                if run_results:
                    # Update result testops_id with actual Qase case IDs
                    clean_results = []
                    for result_data in run_results:
                        xray_case_id = str(result_data.get("testops_id", ""))
                        qase_case_id = self.mappings.get_qase_id(xray_case_id, "case")
                        if qase_case_id:
                            working = copy.deepcopy(result_data)
                            top_xray_att = working.pop("_xray_attachment_ids", None) or []
                            top_hashes: List[str] = []
                            for xid in top_xray_att:
                                ad = attachments_map.get(str(xid))
                                if ad and ad.get("hash"):
                                    top_hashes.append(str(ad["hash"]))

                            step_list = working.get("steps")
                            if isinstance(step_list, list):
                                for step in step_list:
                                    if not isinstance(step, dict):
                                        continue
                                    s_xray = step.pop("_xray_attachment_ids", None) or []
                                    sh: List[str] = []
                                    for xid in s_xray:
                                        ad = attachments_map.get(str(xid))
                                        if ad and ad.get("hash"):
                                            sh.append(str(ad["hash"]))
                                    ex = step.get("execution") or {}
                                    if sh:
                                        ex["attachments"] = list(dict.fromkeys(sh))
                                    sc = ex.get("comment")
                                    if isinstance(sc, str) and sc.strip():
                                        nsc, _ = replace_jira_attachment_refs_in_text(
                                            sc, attachments_map
                                        )
                                        nsc, _ = replace_xray_cloud_attachment_urls_in_text(
                                            nsc, attachments_map
                                        )
                                        ex["comment"] = nsc
                                    step["execution"] = ex

                            clean_result = {
                                k: v
                                for k, v in working.items()
                                if not str(k).startswith("_")
                            }
                            clean_result["testops_id"] = int(qase_case_id)
                            existing_att = clean_result.get("attachments") or []
                            if not isinstance(existing_att, list):
                                existing_att = []
                            clean_result["attachments"] = list(
                                dict.fromkeys(existing_att + top_hashes)
                            )
                            msg = clean_result.get("message")
                            if isinstance(msg, str) and msg.strip():
                                nm, hj = replace_jira_attachment_refs_in_text(
                                    msg, attachments_map
                                )
                                nm, hx = replace_xray_cloud_attachment_urls_in_text(
                                    nm, attachments_map
                                )
                                clean_result["message"] = nm
                                extra = list(dict.fromkeys(hj + hx))
                                if extra:
                                    clean_result["attachments"] = list(
                                        dict.fromkeys(
                                            (clean_result.get("attachments") or []) + extra
                                        )
                                    )
                            clean_results.append(clean_result)
                    
                    if clean_results:
                        results_batches_ok = True
                        batch_size = self.qase_service.RESULT_BULK_CHUNK
                        for i in range(0, len(clean_results), batch_size):
                            batch = clean_results[i:i + batch_size]
                            try:
                                self.qase_service.create_results_bulk_v2(
                                    project_code,
                                    run_id,
                                    batch,
                                )
                                self.stats["results"] += len(batch)
                            except Exception as e:
                                logger.error(f"Error creating results batch: {e}")
                                self.stats["errors"] += 1
                                results_batches_ok = False
                        has_untested = any(
                            (r.get("execution") or {}).get("status") == "untested"
                            for r in clean_results
                        )
                        if results_batches_ok and not has_untested:
                            try:
                                self.qase_service.complete_run(project_code, run_id)
                            except Exception as e:
                                logger.warning(
                                    "Run %s created with results but complete_run failed: %s",
                                    run_id,
                                    e,
                                )
                        elif results_batches_ok and has_untested:
                            logger.info(
                                "Leaving run %s in progress (contains untested / TODO results)",
                                run_id,
                            )
                
                self.stats["runs"] += 1
                
            except Exception as e:
                logger.error(f"Error creating run {run_data.get('title')}: {e}")
                self.stats["errors"] += 1
    
    def _save_mappings(self):
        """Save updated mappings to cache."""
        mappings_dict = self.mappings.to_dict()
        self.cache_manager.save_mappings(mappings_dict)
        logger.info(f"Saved {len(mappings_dict)} ID mappings")
