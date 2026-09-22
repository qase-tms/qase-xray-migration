"""Transformer for Xray projects to Qase projects."""

import re
from typing import Dict, Any, List, Optional
from utils.cache_manager import CacheManager
from utils.logger import get_logger
from models.mappings import MappingStore
from transformers.xray_transformer import BaseTransformer

logger = get_logger(__name__)

# Qase project codes: short uppercase alphanumeric (Jira keys usually satisfy this).
_QASE_CODE_RE = re.compile(r"^[A-Z0-9]{2,10}$")


def _sanitize_project_title(name: str, fallback_key: str) -> str:
    """Drop legacy 'Project <KEY>' placeholder prefix from migration titles."""
    if not isinstance(name, str):
        return fallback_key or "Unknown"
    stripped = name.strip()
    if stripped.lower().startswith("project "):
        rest = stripped[8:].strip()
        if rest:
            return rest
    return stripped or fallback_key or "Unknown"


def _qase_code_from_jira_key(key: str, existing_codes: List[str]) -> Optional[str]:
    if not key or not isinstance(key, str):
        return None
    candidate = key.strip().upper()
    if _QASE_CODE_RE.match(candidate) and candidate not in existing_codes:
        return candidate
    return None


class ProjectTransformer(BaseTransformer):
    """Transforms Xray projects to Qase projects."""
    
    def transform(
        self,
        projects_data: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Transform Xray projects to Qase projects.
        
        Args:
            projects_data: List of Xray project data
        
        Returns:
            List of Qase project payloads
        """
        qase_projects = []
        existing_codes = []
        
        for project in projects_data:
            try:
                project_key = project.get("key", "")
                raw_name = project.get("name", project_key or "Unknown")
                project_name = _sanitize_project_title(raw_name, project_key)
                
                # Title = human name from Jira; code = Jira key when valid, else derived from title
                project_code = _qase_code_from_jira_key(project_key, existing_codes)
                if not project_code:
                    project_code = self.generate_project_code(project_name, existing_codes)
                existing_codes.append(project_code)
                
                qase_project = {
                    "title": project_name,
                    "code": project_code,
                    "description": f"Migrated from Xray project {project_key}",
                    "settings": {
                        "runs": {
                            "auto_complete": False
                        }
                    },
                    "access": "all"
                }
                
                qase_projects.append(qase_project)
                
                # Store mapping
                project_id = str(project.get("id", project_key))
                meta = {"name": project_name, "key": project_key}
                self.mappings.add_mapping(
                    xray_id=project_id,
                    qase_id=project_code,
                    entity_type="project",
                    metadata=meta,
                )
                # Tests use Jira project id; also map project key so cases can resolve if ids differ.
                pk_upper = (project_key or "").strip().upper()
                if pk_upper and pk_upper != project_id:
                    self.mappings.add_mapping(
                        pk_upper,
                        project_code,
                        "project",
                        {**meta, "alias_of": project_id},
                    )
                
                # Verify mapping was stored
                stored_qase_id = self.mappings.get_qase_id(project_id, "project")
                self.logger.debug(f"Transformed project {project_key} (ID: {project_id}) → {project_code}, verified: {stored_qase_id}")
                
            except Exception as e:
                self.logger.error(f"Error transforming project {project.get('key')}: {e}")
                raise
        
        self.logger.info(f"Transformed {len(qase_projects)} projects")
        return qase_projects
