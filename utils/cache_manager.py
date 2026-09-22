"""Cache manager for reading/writing extracted data to disk."""

import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

from .logger import get_logger

logger = get_logger(__name__)


class CacheManager:
    """
    Manages reading and writing extracted data to disk cache.
    
    Cache structure:
    /cache/xray_extraction_[timestamp]/
      /raw_data/
        - projects.json
        - folders.json
        - test_cases.json
        - test_executions.json
        - test_runs.json
        - attachments.json
      /attachments/
        - [downloaded attachment files]
      /mappings/
        - id_mappings.json
      - metadata.json
    """
    
    def __init__(self, cache_dir: Path):
        """
        Initialize cache manager.
        
        Args:
            cache_dir: Path to cache directory (e.g., ./cache/xray_extraction_20260205_143022/)
        """
        self.cache_dir = Path(cache_dir)
        self.raw_data_dir = self.cache_dir / "raw_data"
        self.mappings_dir = self.cache_dir / "mappings"
        self.attachments_dir = self.cache_dir / "attachments"
        
        # Create directory structure
        self.raw_data_dir.mkdir(parents=True, exist_ok=True)
        self.mappings_dir.mkdir(parents=True, exist_ok=True)
        self.attachments_dir.mkdir(parents=True, exist_ok=True)
    
    def save_raw_data(self, entity_type: str, data: Any) -> Path:
        """
        Save raw extracted data to cache.
        
        Args:
            entity_type: Type of entity (projects, folders, test_cases, etc.)
            data: Data to save (will be JSON serialized)
        
        Returns:
            Path to saved file
        """
        filename = f"{entity_type}.json"
        file_path = self.raw_data_dir / filename
        
        logger.debug(f"Saving {entity_type} to {file_path}")
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        
        logger.info(f"Saved {entity_type} ({len(data) if isinstance(data, list) else 'N/A'} items) to cache")
        return file_path
    
    def load_raw_data(self, entity_type: str) -> Optional[Any]:
        """
        Load raw extracted data from cache.
        
        Args:
            entity_type: Type of entity to load
        
        Returns:
            Loaded data or None if file doesn't exist
        """
        filename = f"{entity_type}.json"
        file_path = self.raw_data_dir / filename
        
        if not file_path.exists():
            logger.debug(f"Cache file not found: {file_path}")
            return None
        
        logger.debug(f"Loading {entity_type} from {file_path}")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        logger.info(f"Loaded {entity_type} ({len(data) if isinstance(data, list) else 'N/A'} items) from cache")
        return data
    
    def save_mappings(self, mappings: Dict[str, Any]) -> Path:
        """
        Save ID mappings to cache.
        
        Args:
            mappings: Dictionary of mappings (Xray ID -> Qase ID)
        
        Returns:
            Path to saved file
        """
        file_path = self.mappings_dir / "id_mappings.json"
        
        logger.debug(f"Saving mappings to {file_path}")
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(mappings, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Saved {len(mappings)} mappings to cache")
        return file_path
    
    def load_mappings(self) -> Dict[str, Any]:
        """
        Load ID mappings from cache.
        
        Returns:
            Dictionary of mappings or empty dict if file doesn't exist
        """
        file_path = self.mappings_dir / "id_mappings.json"
        
        if not file_path.exists():
            logger.debug(f"Mappings file not found: {file_path}")
            return {}
        
        logger.debug(f"Loading mappings from {file_path}")
        
        with open(file_path, 'r', encoding='utf-8') as f:
            mappings = json.load(f)
        
        logger.info(f"Loaded {len(mappings)} mappings from cache")
        return mappings
    
    def save_metadata(self, metadata: Dict[str, Any]) -> Path:
        """
        Save extraction metadata.
        
        Args:
            metadata: Metadata dictionary (timestamp, version, stats, etc.)
        
        Returns:
            Path to saved file
        """
        file_path = self.cache_dir / "metadata.json"
        
        logger.debug(f"Saving metadata to {file_path}")
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False, default=str)
        
        return file_path
    
    def load_metadata(self) -> Optional[Dict[str, Any]]:
        """
        Load extraction metadata.
        
        Returns:
            Metadata dictionary or None if file doesn't exist
        """
        file_path = self.cache_dir / "metadata.json"
        
        if not file_path.exists():
            return None
        
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def entity_exists(self, entity_type: str) -> bool:
        """
        Check if cached data exists for an entity type.
        
        Args:
            entity_type: Type of entity to check
        
        Returns:
            True if cached data exists
        """
        filename = f"{entity_type}.json"
        file_path = self.raw_data_dir / filename
        return file_path.exists()
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get statistics about cached data.
        
        Returns:
            Dictionary with cache statistics
        """
        stats = {
            "cache_dir": str(self.cache_dir),
            "entities": {},
            "total_size": 0
        }
        
        for file_path in self.raw_data_dir.glob("*.json"):
            entity_type = file_path.stem
            size = file_path.stat().st_size
            stats["entities"][entity_type] = {
                "file": str(file_path),
                "size_bytes": size,
                "size_mb": round(size / (1024 * 1024), 2)
            }
            stats["total_size"] += size
        
        stats["total_size_mb"] = round(stats["total_size"] / (1024 * 1024), 2)
        
        return stats
    
    @staticmethod
    def create_cache_directory(base_dir: Path = Path("cache")) -> Path:
        """
        Create a new cache directory with timestamp.
        
        Args:
            base_dir: Base directory for caches (default: ./cache)
        
        Returns:
            Path to created cache directory
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        cache_dir = base_dir / f"xray_extraction_{timestamp}"
        cache_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Created cache directory: {cache_dir}")
        return cache_dir
