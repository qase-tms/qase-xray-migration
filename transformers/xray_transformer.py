"""Transformer for Xray Cloud data to Qase format."""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Tuple
import os
import re
import json
from pathlib import Path

from utils.cache_manager import CacheManager
from utils.logger import get_logger
from models.mappings import MappingStore

logger = get_logger(__name__)

_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp")


def replace_jira_attachment_refs_in_text(
    text: str,
    attachment_map: Optional[Dict[str, Dict[str, Any]]],
) -> Tuple[str, List[str]]:
    """
    Replace Jira-style attachment references in plain text / markdown with Qase-friendly links.

    Supported:
    - Wiki images: ``!file.png!`` or ``!file.png|width=200,alt="label"!`` (common in Jira text/export).
    - Storage refs: ``[^file.png]`` (some Confluence/Jira exports).

    Matching uses ``attachment_map`` values' ``filename`` (case-insensitive, basename match).
    Replaces with ``![alt](url)`` or ``[name](url)`` using the **Qase upload URL** (e.g. CDN) once
    ``url`` is set on the map entry; until then the original token is left unchanged.

    Returns (new_text, list of attachment hashes referenced — for linking on the case).
    """
    if not text or not attachment_map:
        return text or "", []

    hashes_out: List[str] = []

    def find_by_filename(name: str) -> Optional[Dict[str, Any]]:
        name = (name or "").strip()
        if not name:
            return None
        want_bn = os.path.basename(name).lower()
        for _aid, att in attachment_map.items():
            if not isinstance(att, dict):
                continue
            fn = (att.get("filename") or "").strip()
            if not fn:
                continue
            if fn == name or fn.lower() == name.lower():
                return att
            if os.path.basename(fn).lower() == want_bn:
                return att
        return None

    def to_markdown(filename: str, link_label: str) -> Optional[str]:
        att = find_by_filename(filename)
        if not att:
            return None
        url = att.get("url")
        if not url:
            return None
        h = att.get("hash")
        if h:
            hashes_out.append(str(h))
        label = (link_label or filename or "file").strip() or filename
        lower = filename.lower()
        is_img = any(lower.endswith(ext) for ext in _IMG_EXT)
        if is_img:
            return f"![{label}]({url})"
        return f"[{label}]({url})"

    def repl_wiki(m: re.Match) -> str:
        inner = (m.group(1) or "").strip()
        if "|" in inner:
            fname, opts = inner.split("|", 1)
        else:
            fname, opts = inner, ""
        fname = fname.strip()
        if not fname:
            return m.group(0)
        alt = fname
        am = re.search(r'alt\s*=\s*"([^"]*)"', opts) or re.search(
            r"alt\s*=\s*'([^']*)'", opts
        )
        if am:
            alt = am.group(1).strip() or alt
        md = to_markdown(fname, alt)
        return md if md is not None else m.group(0)

    def repl_bracket(m: re.Match) -> str:
        filename = (m.group(1) or "").strip()
        if not filename:
            return m.group(0)
        md = to_markdown(filename, filename)
        return md if md is not None else m.group(0)

    out = re.sub(r"!([^!\n]+?)!", repl_wiki, text)
    out = re.sub(r"\[\^([^\]]+)\]", repl_bracket, out)
    return out, list(dict.fromkeys(hashes_out))


# Xray Cloud evidence URLs (us/eu host) → replaced with Qase CDN links after upload
_XRAY_ATT_UUID = (
    r"[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}"
)
_RE_MD_XRAY_LINK = re.compile(
    rf"\[([^\]]*)\]\(\s*(https://[a-zA-Z0-9.-]*xray\.cloud\.getxray\.app/api/v2/attachments/({_XRAY_ATT_UUID}))\s*\)",
    re.IGNORECASE,
)
_RE_BARE_XRAY_ATT_URL = re.compile(
    rf"https://[a-zA-Z0-9.-]*xray\.cloud\.getxray\.app/api/v2/attachments/({_XRAY_ATT_UUID})",
    re.IGNORECASE,
)


def replace_xray_cloud_attachment_urls_in_text(
    text: str,
    attachment_map: Optional[Dict[str, Dict[str, Any]]],
) -> Tuple[str, List[str]]:
    """
    Replace markdown links (or bare URLs) pointing at Xray Cloud ``/api/v2/attachments/{uuid}``
    with Qase ``url`` from ``attachment_map`` (key = attachment uuid string), and collect hashes.
    """
    if not text or not attachment_map:
        return text or "", []

    hashes_out: List[str] = []

    def _att_for_uuid(uid: str) -> Optional[Dict[str, Any]]:
        uid = (uid or "").strip()
        if not uid:
            return None
        att = attachment_map.get(uid)
        if att:
            return att
        uid_l = uid.lower()
        for k, v in attachment_map.items():
            if str(k).strip().lower() == uid_l:
                return v if isinstance(v, dict) else None
        return None

    def _to_md(att: Dict[str, Any], link_label: str) -> Optional[str]:
        url = att.get("url")
        if not url:
            return None
        h = att.get("hash")
        if h:
            hashes_out.append(str(h))
        fn = (att.get("filename") or "file").strip() or "file"
        label = (link_label or "").strip() or fn
        lower = fn.lower()
        is_img = any(lower.endswith(ext) for ext in _IMG_EXT)
        if is_img:
            return f"![{label}]({url})"
        return f"[{label}]({url})"

    def repl_md_link(m: re.Match) -> str:
        label, _full, uid = m.group(1), m.group(2), m.group(3)
        att = _att_for_uuid(uid)
        if not att:
            return m.group(0)
        md = _to_md(att, label)
        return md if md is not None else m.group(0)

    out = _RE_MD_XRAY_LINK.sub(repl_md_link, text)

    def repl_bare(m: re.Match) -> str:
        uid = m.group(1)
        att = _att_for_uuid(uid)
        if not att:
            return m.group(0)
        fn = (att.get("filename") or "file").strip() or "file"
        md = _to_md(att, fn)
        return md if md is not None else m.group(0)

    out = _RE_BARE_XRAY_ATT_URL.sub(repl_bare, out)
    return out, list(dict.fromkeys(hashes_out))


class BaseTransformer(ABC):
    """Base class for transformers with common utilities."""
    
    def __init__(self, cache_manager: CacheManager, mappings: Optional[MappingStore] = None):
        """
        Initialize transformer with cache manager.
        
        Args:
            cache_manager: CacheManager instance for reading extracted data
            mappings: Optional MappingStore for tracking ID mappings
        """
        self.cache_manager = cache_manager
        self.mappings = mappings or MappingStore()
        self.logger = get_logger(self.__class__.__name__)
    
    @abstractmethod
    def transform(self) -> Dict[str, Any]:
        """
        Transform extracted data to target format.
        
        Returns:
            Dictionary with transformation statistics and metadata
        """
        pass
    
    def convert_jira_doc_to_markdown(self, doc: Any) -> str:
        """
        Convert Jira issue text to Markdown for Qase.

        Handles:
        - **Plain strings** (some APIs return description as text/HTML/wiki) — returned trimmed.
        - **ADF** (Atlassian Document Format) ``{"type": "doc", "content": [...]}`` — converted
          with correct **inline** joining (adjacent text runs are concatenated, not split by newlines).

        Older code only accepted dict ADF and joined all nodes with ``\\n``, which broke paragraphs
        and dropped string descriptions entirely.
        """
        if doc is None:
            return ""
        if isinstance(doc, str):
            s = doc.strip()
            if len(s) > 1 and s[0] == "{" and s.endswith("}"):
                try:
                    parsed = json.loads(s)
                    if isinstance(parsed, dict):
                        return self.convert_jira_doc_to_markdown(parsed)
                except json.JSONDecodeError:
                    pass
            return self._strip_simple_html_if_needed(s)
        if not isinstance(doc, dict):
            return str(doc).strip() if doc else ""

        # ADF root or any dict that carries block content
        content = doc.get("content")
        if isinstance(content, list) and content:
            return self._adf_blocks_to_markdown(content)
        # Empty ADF shell
        if doc.get("type") == "doc" or "content" in doc:
            return ""
        # Single stray block node (unlikely)
        if doc.get("type"):
            return self._adf_blocks_to_markdown([doc])
        return ""

    def _strip_simple_html_if_needed(self, s: str) -> str:
        """Best-effort cleanup when Jira returns a string description with HTML tags."""
        if not s or "<" not in s:
            return s
        # Minimal unescape + tag strip (avoid adding heavy deps)
        out = re.sub(r"<br\s*/?>", "\n", s, flags=re.IGNORECASE)
        out = re.sub(r"</p\s*>", "\n\n", out, flags=re.IGNORECASE)
        out = re.sub(r"<[^>]+>", "", out)
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out.strip()

    def _apply_adf_marks(self, text: str, marks: List[Dict[str, Any]]) -> str:
        formatted = text or ""
        for mark in marks or []:
            mark_type = mark.get("type", "")
            attrs = mark.get("attrs") or {}
            if mark_type == "strong":
                formatted = f"**{formatted}**"
            elif mark_type == "em":
                formatted = f"*{formatted}*"
            elif mark_type == "code":
                formatted = f"`{formatted}`"
            elif mark_type == "link":
                href = attrs.get("href", "")
                formatted = f"[{formatted}]({href})"
            elif mark_type == "strike":
                formatted = f"~~{formatted}~~"
            elif mark_type == "underline":
                # Markdown has no standard underline; keep readable
                formatted = formatted
        return formatted

    def _adf_collect_plain_text(self, nodes: List[Dict[str, Any]]) -> str:
        """Deep collect text from unknown nodes (fallback)."""
        parts: List[str] = []
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            if node.get("type") == "text":
                parts.append(self._apply_adf_marks(node.get("text", ""), node.get("marks", [])))
            elif node.get("content"):
                parts.append(self._adf_collect_plain_text(node["content"]))
        return "".join(parts)

    def _adf_inline(self, nodes: List[Dict[str, Any]]) -> str:
        """Render inline / phrasing ADF nodes into one line (newlines only from hardBreak)."""
        if not nodes:
            return ""
        parts: List[str] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_type = node.get("type", "")
            content = node.get("content", [])
            if node_type == "text":
                parts.append(
                    self._apply_adf_marks(node.get("text", ""), node.get("marks", []))
                )
            elif node_type == "hardBreak":
                parts.append("\n")
            elif node_type == "mention":
                attrs = node.get("attrs", {}) or {}
                label = attrs.get("text") or attrs.get("id") or "mention"
                parts.append(f"@{label}")
            elif node_type == "emoji":
                attrs = node.get("attrs", {}) or {}
                short = attrs.get("shortName", "") or attrs.get("text", "")
                parts.append(short or ":emoji:")
            elif node_type == "date":
                attrs = node.get("attrs", {}) or {}
                parts.append(str(attrs.get("timestamp", attrs.get("date", ""))) or "")
            elif node_type == "status":
                attrs = node.get("attrs", {}) or {}
                parts.append(str(attrs.get("text", attrs.get("color", "status"))))
            elif node_type in ("inlineCard", "blockCard"):
                attrs = node.get("attrs", {}) or {}
                url = attrs.get("url", "")
                parts.append(url or "[card]")
            elif content:
                parts.append(self._adf_inline(content))
        return "".join(parts)

    def _adf_code_block_body(self, node: Dict[str, Any]) -> str:
        """Extract all text from a codeBlock node."""
        return self._adf_collect_plain_text(node.get("content", []))

    def _adf_list_item_body(self, list_item: Dict[str, Any]) -> str:
        inner = list_item.get("content", [])
        chunks: List[str] = []
        for block in inner:
            if not isinstance(block, dict):
                continue
            rendered = self._adf_block_to_markdown(block)
            if rendered.strip():
                chunks.append(rendered.strip())
        return "\n".join(chunks)

    def _adf_block_to_markdown(self, node: Dict[str, Any]) -> str:
        if not isinstance(node, dict):
            return ""
        node_type = node.get("type", "")
        content = node.get("content", [])

        if node_type == "paragraph":
            return self._adf_inline(content).strip()
        if node_type == "heading":
            level = int((node.get("attrs") or {}).get("level", 1) or 1)
            level = max(1, min(6, level))
            inner = self._adf_inline(content).strip()
            return f"{'#' * level} {inner}" if inner else ""
        if node_type == "blockquote":
            body = self._adf_blocks_to_markdown(content)
            if not body.strip():
                return ""
            return "\n".join(f"> {line}" for line in body.split("\n"))
        if node_type == "codeBlock":
            lang = (node.get("attrs") or {}).get("language", "") or ""
            code = self._adf_code_block_body(node)
            return f"```{lang}\n{code}\n```"
        if node_type == "rule":
            return "---"
        if node_type == "bulletList":
            lines: List[str] = []
            for child in content:
                if not isinstance(child, dict):
                    continue
                if child.get("type") == "listItem":
                    body = self._adf_list_item_body(child)
                    for i, ln in enumerate(body.split("\n")):
                        prefix = "- " if i == 0 else "  "
                        lines.append(f"{prefix}{ln}")
            return "\n".join(lines)
        if node_type == "orderedList":
            lines = []
            start = int((node.get("attrs") or {}).get("order", 1) or 1)
            idx = start
            for child in content:
                if not isinstance(child, dict):
                    continue
                if child.get("type") == "listItem":
                    body = self._adf_list_item_body(child)
                    for i, ln in enumerate(body.split("\n")):
                        prefix = f"{idx}. " if i == 0 else "   "
                        lines.append(f"{prefix}{ln}")
                    idx += 1
            return "\n".join(lines)
        if node_type == "listItem":
            # Top-level orphan list item
            return "- " + self._adf_list_item_body(node)
        if node_type in ("mediaSingle", "mediaGroup", "expand", "panel", "nestedExpand"):
            return self._adf_blocks_to_markdown(content)
        if node_type == "media":
            attrs = node.get("attrs", {}) or {}
            url = attrs.get("url", "")
            alt = attrs.get("alt", "") or attrs.get("text", "")
            if url:
                return f"![{alt}]({url})" if alt else f"![]({url})"
            att_id = attrs.get("id", "")
            return f"[attachment:{att_id}]" if att_id else ""
        if node_type == "table":
            # Minimal table: rows as markdown lines (no full pipe table alignment)
            return self._adf_table_to_markdown(node)
        if node_type in ("doc",):
            return self._adf_blocks_to_markdown(content)
        if content:
            return self._adf_blocks_to_markdown(content)
        return ""

    def _adf_table_to_markdown(self, table: Dict[str, Any]) -> str:
        rows_out: List[str] = []
        for row in table.get("content", []) or []:
            if not isinstance(row, dict) or row.get("type") != "tableRow":
                continue
            cells: List[str] = []
            for cell in row.get("content", []) or []:
                if not isinstance(cell, dict):
                    continue
                text = self._adf_blocks_to_markdown(cell.get("content", []))
                cells.append(text.replace("\n", " ").strip())
            if cells:
                rows_out.append("| " + " | ".join(cells) + " |")
        return "\n".join(rows_out)

    def _adf_blocks_to_markdown(self, nodes: List[Dict[str, Any]]) -> str:
        parts: List[str] = []
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            block = self._adf_block_to_markdown(node)
            if block.strip():
                parts.append(block.strip())
        return "\n\n".join(parts)
    
    def extract_attachment_ids_from_text(self, text: str) -> List[str]:
        """
        Extract attachment IDs from text (Jira format).
        
        Args:
            text: Text that may contain attachment references
        
        Returns:
            List of attachment IDs found
        """
        if not text:
            return []
        
        # Pattern for Jira attachment references
        # Could be in various formats like [^image.png] or direct references
        # For now, we'll look for common patterns
        attachment_ids = []
        
        pattern = r"\[\^([^\]]+)\]"
        attachment_ids.extend(re.findall(pattern, text))
        for inner in re.findall(r"!([^!\n]+?)!", text):
            fname = inner.split("|", 1)[0].strip()
            if fname:
                attachment_ids.append(fname)
        return attachment_ids
    
    def replace_attachment_references(
        self,
        text: str,
        attachment_map: Dict[str, Dict[str, str]],
    ) -> Tuple[str, List[str]]:
        """Delegate to :func:`replace_jira_attachment_refs_in_text`."""
        return replace_jira_attachment_refs_in_text(text, attachment_map)
    
    def generate_project_code(self, project_name: str, existing_codes: List[str]) -> str:
        """
        Generate a short project code from project name.
        
        Args:
            project_name: Full project name
            existing_codes: List of already used codes
        
        Returns:
            Short code (max 10 characters)
        """
        # Extract first letters of words
        words = re.findall(r'\b\w', project_name)
        code = ''.join(words).upper()
        
        # Remove non-alphabetic characters and truncate to 10
        code = re.sub(r'[^A-Z]', '', code)[:10]
        
        # Ensure minimum length
        if len(code) < 2:
            code = project_name[:10].upper()
            code = re.sub(r'[^A-Z0-9]', '', code)
        
        # Handle duplicates
        base_code = code
        counter = 0
        while code in existing_codes:
            counter += 1
            if counter > 26:
                # Fallback: use first 10 chars of name
                code = re.sub(r'[^A-Z0-9]', '', project_name.upper())[:10]
                break
            code = base_code[:9] + chr(ord('A') + counter - 1)
        
        return code


class XrayTransformer(BaseTransformer):
    """
    Main transformer orchestrator for Xray Cloud data to Qase format.
    
    Coordinates specialized transformers for each entity type:
    - Projects → Qase projects
    - Folders → Qase suites (hierarchical)
    - Test Cases → Qase cases
    - Attachments → Qase attachments (mapping)
    - Test Executions → Qase runs
    - Test Runs → Qase results
    """
    
    def __init__(self, cache_manager: CacheManager, mappings: Optional[MappingStore] = None):
        """Initialize Xray transformer and specialized transformers."""
        # Ensure we have a shared mappings instance for all transformers
        shared_mappings = mappings or MappingStore()
        super().__init__(cache_manager, shared_mappings)
        
        # Lazy import to avoid circular dependency
        from transformers.project_transformer import ProjectTransformer
        from transformers.suite_transformer import SuiteTransformer
        from transformers.attachment_transformer import AttachmentTransformer
        from transformers.case_transformer import CaseTransformer
        from transformers.run_transformer import RunTransformer
        
        # Initialize specialized transformers with the same shared mappings instance
        self.project_transformer = ProjectTransformer(cache_manager, shared_mappings)
        self.suite_transformer = SuiteTransformer(cache_manager, shared_mappings)
        self.attachment_transformer = AttachmentTransformer(cache_manager, shared_mappings)
        self.case_transformer = CaseTransformer(cache_manager, shared_mappings)
        self.run_transformer = RunTransformer(cache_manager, shared_mappings)
        
        self.transformed_data = {}
        self.stats = {
            "projects": 0,
            "suites": 0,
            "cases": 0,
            "runs": 0,
            "results": 0,
            "attachments_mapped": 0,
            "errors": 0
        }
    
    def transform(self) -> Dict[str, Any]:
        """
        Transform all extracted Xray data to Qase format.
        
        Returns:
            Dictionary with transformation statistics
        """
        self.logger.info("Starting Xray to Qase transformation...")
        
        try:
            # Load raw data
            projects_data = self.cache_manager.load_raw_data("projects")
            folders_data = self.cache_manager.load_raw_data("folders")
            test_cases_data = self.cache_manager.load_raw_data("test_cases")
            attachments_data = self.cache_manager.load_raw_data("attachments")
            
            if not projects_data:
                raise ValueError("No projects data found in cache")
            if not test_cases_data:
                raise ValueError("No test cases data found in cache")
            
            # Transform in order
            self.logger.info("Step 1: Transforming projects...")
            qase_projects = self.project_transformer.transform(projects_data)
            self.stats["projects"] = len(qase_projects)
            
            # Debug: Check mappings after project transformation
            self.logger.debug(f"Mappings after projects: {list(self.mappings.mappings.keys())}")
            
            self.logger.info("Step 2: Transforming folders to suites...")
            qase_suites = self.suite_transformer.transform(folders_data, projects_data)
            self.stats["suites"] = sum(len(suites) for suites in qase_suites.values())
            
            self.logger.info("Step 3: Mapping attachments...")
            qase_attachments_map = self.attachment_transformer.transform(attachments_data or [])
            self.stats["attachments_mapped"] = len(qase_attachments_map)
            
            self.logger.info("Step 4: Transforming test cases...")
            # Debug: Check what project IDs we're looking for
            unique_project_ids = set(str(tc.get("projectId", "")) for tc in test_cases_data[:5])
            self.logger.debug(f"Sample project IDs from test cases: {unique_project_ids}")
            self.logger.debug(f"Available mappings: {list(self.mappings.mappings.keys())}")
            
            qase_cases = self.case_transformer.transform(
                test_cases_data,
                qase_suites,
                qase_attachments_map
            )
            self.stats["cases"] = sum(len(cases) for cases in qase_cases.values())
            
            # Check if test executions exist
            try:
                executions_data = self.cache_manager.load_raw_data("test_executions")
                if executions_data:
                    self.logger.info("Step 5: Transforming test executions and runs...")
                    qase_runs, qase_results = self.run_transformer.transform(
                        executions_data,
                        qase_cases
                    )
                    self.stats["runs"] = len(qase_runs)
                    self.stats["results"] = len(qase_results)
                    self.transformed_data["runs"] = qase_runs
                    self.transformed_data["results"] = qase_results
                else:
                    self.logger.info("No test executions found, skipping runs/results transformation")
            except FileNotFoundError:
                self.logger.info("No test executions found, skipping runs/results transformation")
            
            # Store transformed data
            self.transformed_data["projects"] = qase_projects
            self.transformed_data["suites"] = qase_suites
            self.transformed_data["cases"] = qase_cases
            self.transformed_data["attachments_map"] = qase_attachments_map
            
            # Save transformed data
            self._save_transformed_data()
            
            # Save mappings
            self._save_mappings()
            
            self.logger.info("Transformation complete!")
            return {
                "status": "success",
                "stats": self.stats,
                "transformed_data": {
                    "projects": len(qase_projects),
                    "suites": self.stats["suites"],
                    "cases": self.stats["cases"],
                    "runs": len(self.transformed_data.get("runs", [])),
                    "results": len(self.transformed_data.get("results", []))
                }
            }
            
        except Exception as e:
            self.logger.error(f"Transformation failed: {e}", exc_info=True)
            self.stats["errors"] += 1
            return {
                "status": "error",
                "error": str(e),
                "stats": self.stats
            }
    
    def _save_transformed_data(self):
        """Save transformed data to cache."""
        transformed_dir = self.cache_manager.cache_dir / "transformed"
        transformed_dir.mkdir(exist_ok=True)
        
        # Save each entity type
        for entity_type, data in self.transformed_data.items():
            file_path = transformed_dir / f"{entity_type}.json"
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        
        self.logger.info(f"Saved transformed data to {transformed_dir}")
    
    def _save_mappings(self):
        """Save ID mappings to cache."""
        mappings_dict = self.mappings.to_dict()
        self.cache_manager.save_mappings(mappings_dict)
        self.logger.info(f"Saved {len(mappings_dict)} ID mappings")
