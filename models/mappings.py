"""ID mapping models for tracking Xray to Qase ID translations."""

from dataclasses import dataclass, field
from typing import Dict, Optional, Any


@dataclass
class IDMapping:
    """Maps a single Xray ID to Qase ID."""
    xray_id: str
    qase_id: Optional[str] = None
    entity_type: str = "unknown"  # project, folder, test, execution, run, attachment
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MappingStore:
    """Stores all ID mappings for the migration."""
    mappings: Dict[str, IDMapping] = field(default_factory=dict)
    
    def add_mapping(self, xray_id: str, qase_id: Optional[str], entity_type: str, metadata: Optional[Dict] = None):
        """
        Add or update a mapping.

        Jira numeric IDs can collide across entity kinds (e.g. project id vs issue id). If the storage key
        is already used by another entity_type, the new entry is stored under ``{entity_type}:{xray_id}``.
        """
        key = str(xray_id)
        existing = self.mappings.get(key)
        if existing is not None and existing.entity_type != entity_type:
            key = f"{entity_type}:{key}"
        self.mappings[key] = IDMapping(
            xray_id=key,
            qase_id=qase_id,
            entity_type=entity_type,
            metadata=metadata or {}
        )
    
    def get_qase_id(self, xray_id: str, entity_type: Optional[str] = None) -> Optional[str]:
        """
        Get Qase ID for a given Xray ID.

        When ``entity_type`` is set, only a mapping of that type is returned (avoids project vs case
        collisions on the same numeric id). Also checks the namespaced key ``{entity_type}:{id}``.
        """
        sid = str(xray_id)
        if entity_type is None:
            mapping = self.mappings.get(sid)
            return mapping.qase_id if mapping else None
        m = self.mappings.get(sid)
        if m is not None and m.entity_type == entity_type:
            return m.qase_id
        alt = f"{entity_type}:{sid}"
        m = self.mappings.get(alt)
        if m is not None and m.entity_type == entity_type:
            return m.qase_id
        return None
    
    def get_mapping(self, xray_id: str) -> Optional[IDMapping]:
        """Get full mapping for a given Xray ID."""
        return self.mappings.get(xray_id)
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            xray_id: {
                "qase_id": mapping.qase_id,
                "entity_type": mapping.entity_type,
                "metadata": mapping.metadata
            }
            for xray_id, mapping in self.mappings.items()
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "MappingStore":
        """Create from dictionary (JSON deserialization)."""
        store = cls()
        for xray_id, mapping_data in data.items():
            store.mappings[xray_id] = IDMapping(
                xray_id=xray_id,
                qase_id=mapping_data.get("qase_id"),
                entity_type=mapping_data.get("entity_type", "unknown"),
                metadata=mapping_data.get("metadata", {})
            )
        return store
