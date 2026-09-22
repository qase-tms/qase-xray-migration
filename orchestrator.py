"""Orchestrator for coordinating migration phases."""

from typing import Dict, Any, Optional
from pathlib import Path

from utils.cache_manager import CacheManager
from utils.config import qase_api_url, selected_projects
from utils.graphql_client import GraphQLClient
from utils.logger import get_logger
from extractors.xray_cloud_extractor import XrayCloudExtractor
from transformers.xray_transformer import XrayTransformer
from loaders.qase_loader import QaseLoader
from services.qase_dry_run import DryRunQaseService
from services.qase_service import QaseService
from models.mappings import MappingStore

logger = get_logger(__name__)


class MigrationOrchestrator:
    """
    Orchestrates the migration process across all phases.
    
    Phases:
    1. EXTRACT - Pull data from Xray Cloud
    2. TRANSFORM - Map Xray data to Qase format
    3. LOAD - Import data into Qase
    """
    
    def __init__(self, config: Dict[str, Any], cache_dir: Optional[Path] = None, dry_run: bool = False):
        """
        Initialize orchestrator.
        
        Args:
            config: Configuration dictionary
            cache_dir: Optional cache directory (if None, creates new one)
        """
        self.config = config
        self.dry_run = dry_run
        self.logger = get_logger(__name__)
        
        # Validate config
        self._validate_config()
        
        # Setup cache
        if cache_dir:
            self.cache_manager = CacheManager(cache_dir)
        else:
            base_cache_dir = Path(config.get("cache.dir", "./cache"))
            self.cache_dir = CacheManager.create_cache_directory(base_cache_dir)
            self.cache_manager = CacheManager(self.cache_dir)
        
        # Setup GraphQL client
        self.client = GraphQLClient(
            client_id=config.get("xray.client_id"),
            client_secret=config.get("xray.client_secret"),
            jira_url=config.get("jira.url"),
            jira_email=config.get("jira.email"),
            jira_api_token=config.get("jira.api_token"),
            jira_oauth_client_id=config.get("jira.oauth_client_id"),
            jira_oauth_client_secret=config.get("jira.oauth_client_secret")
        )
        
        # Setup extractor
        self.extractor = XrayCloudExtractor(self.cache_manager, self.client)
        
        # Setup transformer
        # Load existing mappings if available
        self.mappings = None
        try:
            mappings_path = self.cache_manager.mappings_dir / "id_mappings.json"
            if mappings_path.exists():
                import json
                with open(mappings_path, "r") as f:
                    mappings_data = json.load(f)
                    self.mappings = MappingStore.from_dict(mappings_data)
        except Exception as e:
            self.logger.warning(f"Could not load existing mappings: {e}")
        
        self.transformer = XrayTransformer(self.cache_manager, self.mappings)
        
        # Setup loader (if Qase credentials provided)
        self.loader = None
        if config.get("qase.api_token"):
            if dry_run:
                logger.info("DRY RUN: reading everything, writing nothing to Qase")
                print("\n\033[33m▲ DRY RUN: nothing will be written to Qase\033[0m\n", flush=True)
            service_cls = DryRunQaseService if dry_run else QaseService
            qase_service = service_cls(
                api_token=config.get("qase.api_token"),
                qase_host=qase_api_url(config)
            )
            # Use the same mappings instance as transformer
            if self.mappings is None:
                self.mappings = self.transformer.mappings
            self.loader = QaseLoader(
                self.cache_manager,
                qase_service,
                self.mappings,
                preserve_xray_case_ids=bool(config.get("cases.preserve_ids", False)),
            )
    
    def _validate_config(self):
        """Validate configuration file. preflight.py does this far more thoroughly."""
        required = {
            "xray.client_id": "Xray Cloud client id",
            "xray.client_secret": "Xray Cloud client secret",
            "jira.url": "Jira site URL",
            "qase.api_token": "Qase API token",
        }
        for key, label in required.items():
            if not str(self.config.get(key) or "").strip():
                raise ValueError(f"Missing required config field: {key} ({label})")

        if not self.config.get("projects.import_all") and not selected_projects(self.config):
            raise ValueError(
                "No projects selected. List Jira project keys in projects.import, "
                "or set projects.import_all to true."
            )
    
    def extract(self) -> Dict[str, Any]:
        """
        Run extraction phase.
        
        Returns:
            Extraction statistics
        """
        self.logger.info("Starting EXTRACT phase...")
        return self.extractor.extract(self.config)
    
    def transform(self) -> Dict[str, Any]:
        """
        Run transformation phase.
        
        Returns:
            Transformation statistics
        """
        self.logger.info("Starting TRANSFORM phase...")
        return self.transformer.transform()
    
    def load(self) -> Dict[str, Any]:
        """
        Run load phase.
        
        Returns:
            Load statistics
        """
        self.logger.info("Starting LOAD phase...")
        
        if not self.loader:
            raise ValueError(
                "Qase credentials not configured. "
                "Please provide 'qase_api_token' and 'qase_host' in config."
            )
        
        return self.loader.load()
    
    def migrate(self) -> Dict[str, Any]:
        """
        Run all phases in sequence.
        
        Returns:
            Combined statistics from all phases
        """
        self.logger.info("=" * 60)
        self.logger.info("STARTING FULL MIGRATION")
        self.logger.info("=" * 60)
        
        results = {}
        
        # Phase 1: Extract
        results["extract"] = self.extract()
        
        # Phase 2: Transform
        results["transform"] = self.transform()
        
        # Phase 3: Load
        results["load"] = self.load()
        
        self.logger.info("=" * 60)
        self.logger.info("MIGRATION COMPLETE")
        self.logger.info("=" * 60)
        
        return results
