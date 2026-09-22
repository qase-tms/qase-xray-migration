"""Base extractor interface."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any, List, Optional

from utils.cache_manager import CacheManager
from utils.logger import get_logger

logger = get_logger(__name__)


class BaseExtractor(ABC):
    """Base class for extractors."""
    
    def __init__(self, cache_manager: CacheManager):
        """
        Initialize extractor with cache manager.
        
        Args:
            cache_manager: CacheManager instance for saving extracted data
        """
        self.cache_manager = cache_manager
        self.logger = get_logger(self.__class__.__name__)
    
    @abstractmethod
    def extract(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract data from source system.
        
        Args:
            config: Configuration dictionary
        
        Returns:
            Dictionary with extraction statistics and metadata
        """
        pass
    
    def save_extraction_metadata(
        self,
        stats: Dict[str, Any],
        xray_version: Optional[str] = None
    ) -> None:
        """
        Save extraction metadata to cache.
        
        Args:
            stats: Extraction statistics
            xray_version: Optional Xray version information
        """
        from datetime import datetime
        
        metadata = {
            "extraction_timestamp": datetime.now().isoformat(),
            "xray_version": xray_version,
            "stats": stats,
            "cache_dir": str(self.cache_manager.cache_dir)
        }
        
        self.cache_manager.save_metadata(metadata)
        self.logger.info("Saved extraction metadata")
