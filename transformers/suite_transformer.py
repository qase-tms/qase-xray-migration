"""Transformer for Xray folders to Qase suites."""

from typing import Dict, Any, List
from utils.cache_manager import CacheManager
from utils.logger import get_logger
from models.mappings import MappingStore
from transformers.xray_transformer import BaseTransformer

logger = get_logger(__name__)


class SuiteTransformer(BaseTransformer):
    """Transforms Xray folders to Qase suites (hierarchical)."""
    
    def transform(
        self,
        folders_data: List[Dict[str, Any]],
        projects_data: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Transform Xray folders to Qase suites (hierarchical).
        
        Args:
            folders_data: List of Xray folder data
            projects_data: List of Xray project data
        
        Returns:
            Dictionary mapping project_code → list of suite payloads
        """
        # Group folders by project
        project_folders: Dict[str, List[Dict[str, Any]]] = {}
        
        # Create project_id → project_code mapping
        project_id_to_code = {}
        existing_project_codes = sorted(
            {
                m.qase_id
                for m in self.mappings.mappings.values()
                if m.entity_type == "project" and m.qase_id
            }
        )
        for project in projects_data:
            pj_key = project.get("key", "") or ""
            project_id = str(project.get("id", pj_key))
            project_name = project.get("name", pj_key or "Unknown")
            project_code = self.mappings.get_qase_id(project_id, "project")
            if not project_code:
                # Generate code if not mapped yet (same path as cases must share this mapping)
                project_code = self.generate_project_code(project_name, list(existing_project_codes))
                self.mappings.add_mapping(
                    xray_id=project_id,
                    qase_id=project_code,
                    entity_type="project",
                )
                existing_project_codes.append(project_code)
            project_id_to_code[project_id] = project_code
        
        # Group folders by project
        for folder in folders_data:
            project_id = str(folder.get("projectId", ""))
            project_code = project_id_to_code.get(project_id)
            if project_code:
                if project_code not in project_folders:
                    project_folders[project_code] = []
                project_folders[project_code].append(folder)
        
        # Transform folders to suites (build hierarchy)
        qase_suites: Dict[str, List[Dict[str, Any]]] = {}
        
        for project_code, folders in project_folders.items():
            suites = self._build_suite_hierarchy(folders)
            qase_suites[project_code] = suites
        
        total_suites = sum(len(suites) for suites in qase_suites.values())
        self.logger.info(f"Transformed {total_suites} folders to suites across {len(qase_suites)} projects")
        
        return qase_suites
    
    def _build_suite_hierarchy(
        self,
        folders: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Build hierarchical suite structure from folders.
        
        Args:
            folders: List of folder data for a project
        
        Returns:
            List of suite payloads with parent relationships
        """
        suites = []
        
        # Sort folders by path depth
        folders_sorted = sorted(folders, key=lambda f: f.get("path", "").count("/"))
        
        # Build parent mapping
        path_to_suite: Dict[str, Dict[str, Any]] = {}
        
        for folder in folders_sorted:
            path = folder.get("path", "")
            name = folder.get("name", "Unnamed Suite")
            
            # Convert path to suite hierarchy
            suite = {
                "title": name,
                "description": f"Migrated from Xray folder: {path}",
                "preconditions": "",
                "parent_id": None  # Will be set if parent exists
            }
            
            # Find parent suite
            parent_path = "/".join(path.rstrip("/").split("/")[:-1])
            if parent_path and parent_path in path_to_suite:
                parent_suite = path_to_suite[parent_path]
                # Parent ID will be set during creation, for now we track path
                suite["_parent_path"] = parent_path
            
            path_to_suite[path] = suite
            suites.append(suite)
        
        return suites
