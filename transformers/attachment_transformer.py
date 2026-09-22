"""Transformer for mapping Xray attachments to Qase format."""

from typing import Dict, Any, List
from utils.cache_manager import CacheManager
from utils.logger import get_logger
from models.mappings import MappingStore
from transformers.xray_transformer import BaseTransformer

logger = get_logger(__name__)


class AttachmentTransformer(BaseTransformer):
    """Maps Xray attachments to Qase attachment format."""
    
    def transform(
        self,
        attachments_data: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, str]]:
        """
        Map Xray attachments to Qase attachment format.
        
        Args:
            attachments_data: List of Xray attachment data
        
        Returns:
            Dictionary mapping Xray attachment ID → Qase attachment object
        """
        attachments_map = {}
        
        for attachment in attachments_data:
            try:
                xray_id = str(attachment.get("id", ""))
                filename = attachment.get("filename", "")
                local_path = attachment.get("local_path") or attachment.get("local_filename", "")
                
                # Store mapping (hash will be set after upload to Qase)
                attachments_map[xray_id] = {
                    "filename": filename,
                    "local_path": local_path,
                    "hash": None,  # Will be set after upload
                    "url": None    # Will be set after upload
                }
                
            except Exception as e:
                self.logger.error(f"Error mapping attachment {attachment.get('id')}: {e}")
                raise
        
        self.logger.info(f"Mapped {len(attachments_map)} attachments")
        return attachments_map
