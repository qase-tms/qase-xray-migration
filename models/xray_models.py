"""Data models for Xray Cloud entities."""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime


@dataclass
class XrayTestStep:
    """Represents a test step in Xray."""
    id: str
    data: Optional[str] = None
    action: Optional[str] = None
    result: Optional[str] = None


@dataclass
class XrayTestRunStep:
    """Represents a test run step result in Xray."""
    status: Optional[str] = None
    actual_result: Optional[str] = None
    comment: Optional[str] = None


@dataclass
class XrayProject:
    """Represents a Jira/Xray project."""
    key: str
    id: str
    name: str
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class XrayFolder:
    """Represents a folder in Xray."""
    project_id: str
    path: str
    name: str
    tests_count: int = 0
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class XrayTest:
    """Represents a test case in Xray."""
    issue_id: str
    project_id: str
    test_type: Optional[str] = None
    folder_path: Optional[str] = None
    steps: List[XrayTestStep] = field(default_factory=list)
    summary: Optional[str] = None
    description: Optional[str] = None
    labels: List[str] = field(default_factory=list)
    attachments: List[str] = field(default_factory=list)  # Attachment IDs
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class XrayTestExecution:
    """Represents a test execution in Xray."""
    issue_id: str
    project_id: str
    summary: Optional[str] = None
    description: Optional[str] = None
    test_runs: List["XrayTestRun"] = field(default_factory=list)
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class XrayTestRun:
    """Represents an individual test run result in Xray."""
    id: str
    test_issue_id: str
    execution_issue_id: str
    status: Optional[str] = None
    status_color: Optional[str] = None
    started_on: Optional[datetime] = None
    finished_on: Optional[datetime] = None
    comment: Optional[str] = None
    steps: List[XrayTestRunStep] = field(default_factory=list)
    raw_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class XrayAttachment:
    """Represents an attachment in Xray/Jira."""
    id: str
    filename: str
    content_type: Optional[str] = None
    size: Optional[int] = None
    content_url: Optional[str] = None
    local_path: Optional[str] = None  # Path after download
    raw_data: Dict[str, Any] = field(default_factory=dict)
