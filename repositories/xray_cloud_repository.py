"""Repository for accessing Xray Cloud GraphQL API."""

import re
from typing import List, Dict, Any, Optional

from tqdm import tqdm

from utils.graphql_client import GraphQLClient
from utils.logger import get_logger
from models.xray_models import (
    XrayProject,
    XrayFolder,
    XrayTest,
    XrayTestExecution,
    XrayTestRun,
    XrayTestStep,
    XrayTestRunStep,
)

logger = get_logger(__name__)


def _gherkin_lines_to_synthetic_steps(issue_id: str, gherkin: str) -> List[Dict[str, Any]]:
    """
    Build Xray-shaped step dicts from Gherkin text. Xray stores Cucumber specs in ``gherkin``,
    not in ``steps``; GraphQL ``steps`` is often empty for those tests.
    """
    steps: List[Dict[str, Any]] = []
    iid = (issue_id or "x").strip() or "x"
    for raw in (gherkin or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("@"):
            continue
        # Scenario outline example rows / tables
        if re.match(r"^\|.*\|$", line):
            continue
        if re.match(r"^(Given|When|Then|And|But)\s+", line, re.IGNORECASE):
            steps.append(
                {
                    "id": f"gherkin-{iid}-{len(steps) + 1}",
                    "data": "",
                    "action": line,
                    "result": "",
                }
            )
    return steps


def _enrich_test_steps_from_xray_definitions(test: Dict[str, Any]) -> None:
    """If ``steps`` is empty, fill from ``gherkin`` or ``unstructured`` (GraphQL Test fields)."""
    if not isinstance(test, dict):
        return
    steps = test.get("steps")
    has_real_steps = False
    if isinstance(steps, list):
        for s in steps:
            if not isinstance(s, dict):
                continue
            if s.get("action") or s.get("result") or s.get("data"):
                has_real_steps = True
                break
    if has_real_steps:
        return
    iid = str(test.get("issueId") or "x").strip() or "x"
    gh = (test.get("gherkin") or "").strip()
    if gh:
        syn = _gherkin_lines_to_synthetic_steps(iid, gh)
        if syn:
            test["steps"] = syn
            logger.debug(
                "Synthesized %s step(s) from gherkin for test issueId=%s",
                len(syn),
                iid,
            )
        return
    uns = (test.get("unstructured") or "").strip()
    if uns:
        test["steps"] = [
            {
                "id": f"unstructured-{iid}-1",
                "data": "",
                "action": uns if len(uns) <= 16000 else (uns[:15997] + "..."),
                "result": "",
            }
        ]
        logger.debug("Synthesized 1 step from unstructured for test issueId=%s", iid)


_FETCH_TEST_RUNS_QUERY = """
query GetTestExecutionTestRuns($issueId: String!, $limit: Int!, $start: Int!) {
  getTestExecution(issueId: $issueId) {
    issueId
    testRuns(limit: $limit, start: $start) {
      total
      start
      limit
      results {
        id
        status {
          name
          color
        }
        startedOn
        finishedOn
        test {
          issueId
        }
        comment
        defects
        evidence {
          id
          filename
          downloadLink
          storedInJira
          createdOn
        }
        steps {
          id
          status {
            name
          }
          action
          data
          result
          actualResult
          comment
          defects
          evidence {
            id
            filename
            downloadLink
            storedInJira
          }
          attachments {
            id
            filename
            downloadLink
            storedInJira
          }
        }
      }
    }
  }
}
"""


class XrayCloudRepository:
    """
    Repository for accessing Xray Cloud data via GraphQL API.
    
    Handles pagination, data transformation, and error handling.
    """
    
    # GraphQL query limits (Xray caps per-request limit at 100 for most connections)
    MAX_LIMIT = 100  # Max items per query

    def __init__(self, graphql_client: GraphQLClient):
        """
        Initialize repository with GraphQL client.
        
        Args:
            graphql_client: Configured GraphQLClient instance
        """
        self.client = graphql_client
    
    def get_projects(self, project_keys: List[str]) -> List[Dict[str, Any]]:
        """
        Get project information from Jira REST API.
        
        Args:
            project_keys: List of project keys to fetch
        
        Returns:
            List of project data dictionaries
        """
        logger.info(f"Fetching {len(project_keys)} projects from Jira...")
        projects = []
        
        for key in tqdm(project_keys, desc="Fetching projects"):
            last_err: Optional[Exception] = None
            for endpoint in (
                f"/rest/api/3/project/{key}",
                f"/rest/api/2/project/{key}",
            ):
                try:
                    project_data = self.client.get_jira_rest_api(endpoint)
                    projects.append(project_data)
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
            if last_err is not None:
                logger.error(f"Failed to fetch project {key}: {last_err}")
        
        logger.info(f"Fetched {len(projects)} projects")
        return projects
    
    def get_tests(
        self,
        project_key: str,
        folder_path: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get all tests for a project using GraphQL.
        
        Args:
            project_key: Jira project key
            folder_path: Optional folder path to filter tests
        
        Returns:
            List of test data dictionaries
        """
        logger.info(f"Fetching tests for project {project_key}...")
        
        all_tests: List[Dict[str, Any]] = []
        start = 0
        limit = self.MAX_LIMIT
        
        jql = f"project = '{project_key}'"
        if folder_path:
            jql += f" AND folder = '{folder_path}'"
        
        query = """
        query GetTests($jql: String!, $limit: Int!, $start: Int!) {
          getTests(jql: $jql, limit: $limit, start: $start) {
            total
            start
            limit
            results {
              issueId
              projectId
              testType {
                name
              }
              folder {
                path
              }
              gherkin
              scenarioType
              unstructured
              steps {
                id
                data
                action
                result
              }
              jira(fields: ["key", "summary", "description", "labels", "attachment", "project", "priority"])
            }
          }
        }
        """
        
        while True:
            try:
                variables = {
                    "jql": jql,
                    "limit": limit,
                    "start": start,
                }
                
                response = self.client.execute_query(query, variables)
                tests_data = response.get("getTests", {})
                
                results = tests_data.get("results", [])
                total = tests_data.get("total", 0)
                start_pos = tests_data.get("start", start)
                limit_val = tests_data.get("limit", limit)

                for row in results:
                    _enrich_test_steps_from_xray_definitions(row)

                all_tests.extend(results)
                
                logger.info(
                    f"Page: start={start_pos}, limit={limit_val}, fetched={len(results)}, "
                    f"total_fetched={len(all_tests)}, API_total={total}"
                )
                logger.debug(
                    f"Fetched {len(results)} tests (total fetched: {len(all_tests)}, API total: {total})"
                )
                
                if len(results) < limit:
                    logger.debug(
                        f"Got fewer results ({len(results)}) than limit ({limit}), pagination complete"
                    )
                    break
                
                if total > 0 and len(all_tests) >= total:
                    logger.debug(f"Fetched all {total} tests according to API")
                    break
                
                if len(results) == 0:
                    logger.debug("No more results, pagination complete")
                    break
                
                start += limit

            except Exception as e:
                logger.error(f"Error fetching tests (start={start}): {e}")
                break
        
        logger.info(f"Fetched {len(all_tests)} tests for project {project_key}")
        return all_tests

    def _fetch_all_test_runs_for_execution(self, execution_issue_id: str) -> Dict[str, Any]:
        """
        Load all test runs for one execution using testRuns(limit, start) until a short page
        or total is reached. Per-execution pagination avoids silent truncation when bulk
        getTestExecutions returns many executions (shared variables would not apply per row).
        """
        all_results: List[Dict[str, Any]] = []
        start = 0
        limit = self.MAX_LIMIT
        page = 1
        reported_total: Optional[int] = None

        while True:
            try:
                variables = {
                    "issueId": execution_issue_id,
                    "limit": limit,
                    "start": start,
                }
                response = self.client.execute_query(_FETCH_TEST_RUNS_QUERY, variables)
                tex = response.get("getTestExecution")
                if not tex:
                    logger.warning(
                        "getTestExecution returned no data for execution issueId=%s",
                        execution_issue_id,
                    )
                    break

                tr_block = tex.get("testRuns") or {}
                results = tr_block.get("results") or []
                if reported_total is None:
                    t = tr_block.get("total")
                    if t is not None:
                        reported_total = int(t)

                logger.info(
                    "Fetching test run page %s for execution %s (%s runs so far)",
                    page,
                    execution_issue_id,
                    len(all_results) + len(results),
                )
                all_results.extend(results)

                if len(results) < limit:
                    break
                if reported_total is not None and len(all_results) >= reported_total:
                    break
                if len(results) == 0:
                    break

                start += limit
                page += 1

            except Exception as e:
                logger.error(
                    "Error fetching test runs for execution %s (start=%s): %s",
                    execution_issue_id,
                    start,
                    e,
                )
                break

        out_total = reported_total if reported_total is not None else len(all_results)
        return {"total": out_total, "results": all_results}
    
    def get_test_executions(
        self,
        project_key: str
    ) -> List[Dict[str, Any]]:
        """
        Get all test executions for a project using GraphQL.
        
        Args:
            project_key: Jira project key
        
        Returns:
            List of test execution data dictionaries
        """
        logger.info(f"Fetching test executions for project {project_key}...")
        
        all_executions = []
        start = 0
        limit = 50  # Execution list page size (test runs loaded separately per execution)
        
        jql = f"project = '{project_key}'"
        
        # testRuns are loaded per execution via getTestExecution + offset pagination (see
        # _fetch_all_test_runs_for_execution); bulk getTestExecutions shares one limit/start
        # across all rows, so nested pagination cannot be correct for batched results.
        query = """
        query GetTestExecutions($jql: String!, $limit: Int!, $start: Int!) {
          getTestExecutions(jql: $jql, limit: $limit, start: $start) {
            total
            results {
              issueId
              projectId
              jira(fields: ["summary", "description", "project"])
            }
          }
        }
        """
        
        while True:
            try:
                variables = {
                    "jql": jql,
                    "limit": limit,
                    "start": start
                }
                
                response = self.client.execute_query(query, variables)
                executions_data = response.get("getTestExecutions", {})
                
                results = executions_data.get("results", [])
                total = executions_data.get("total", 0)
                
                all_executions.extend(results)
                
                logger.debug(f"Fetched {len(results)} test executions (total fetched: {len(all_executions)}, API total: {total})")
                
                # Check if we've fetched all executions
                if len(results) < limit:
                    logger.debug(f"Got fewer results ({len(results)}) than limit ({limit}), pagination complete")
                    break
                if total > 0 and len(all_executions) >= total:
                    logger.debug(f"Fetched all {total} executions according to API")
                    break
                if len(results) == 0:
                    logger.debug("No more results, pagination complete")
                    break
                
                start += limit

            except Exception as e:
                logger.error(f"Error fetching test executions (start={start}): {e}")
                break
        
        logger.info(f"Fetched {len(all_executions)} test executions for project {project_key}")

        for execution in tqdm(all_executions, desc="Test runs per execution"):
            issue_id = execution.get("issueId")
            if issue_id is None:
                execution["testRuns"] = {"total": 0, "results": []}
                logger.warning("Test execution missing issueId; skipping test runs")
                continue
            execution["testRuns"] = self._fetch_all_test_runs_for_execution(str(issue_id))

        return all_executions
    
    def get_folders(
        self,
        project_id: str,
        base_path: str = "/"
    ) -> List[Dict[str, Any]]:
        """
        Get folders for a project recursively.
        
        Args:
            project_id: Xray project ID
            base_path: Base folder path to start from (default: "/")
        
        Returns:
            List of folder data dictionaries
        """
        logger.info(f"Fetching folders for project {project_id} (path: {base_path})...")
        
        folders = []
        
        query = """
        query GetFolder($projectId: String!, $path: String!) {
          getFolder(projectId: $projectId, path: $path) {
            name
            path
            testsCount
            folders {
              name
              path
              testsCount
            }
          }
        }
        """
        
        def fetch_folder_recursive(path: str):
            """Recursively fetch folders."""
            try:
                variables = {
                    "projectId": project_id,
                    "path": path
                }
                
                response = self.client.execute_query(query, variables)
                folder_data = response.get("getFolder")
                
                if folder_data:
                    folders.append(folder_data)
                    
                    # Recursively fetch subfolders
                    subfolders = folder_data.get("folders", [])
                    if subfolders:
                        logger.debug(f"Found {len(subfolders)} subfolders in {path}")
                        for subfolder in subfolders:
                            subfolder_path = subfolder.get("path", "")
                            if subfolder_path:
                                fetch_folder_recursive(subfolder_path)
                    else:
                        logger.debug(f"No subfolders found in {path}")
                else:
                    logger.warning(f"No folder data returned for path: {path}, project: {project_id}")
                        
            except Exception as e:
                logger.error(f"Error fetching folder {path} for project {project_id}: {e}")
                logger.debug(f"Folder query variables: projectId={project_id}, path={path}", exc_info=True)
        
        try:
            fetch_folder_recursive(base_path)
        except Exception as e:
            logger.error(f"Error fetching folders: {e}")
        
        logger.info(f"Fetched {len(folders)} folders for project {project_id}")
        return folders
    
    def get_attachments(self, attachment_ids: List[str]) -> List[Dict[str, Any]]:
        """
        Get attachment metadata and download attachments from Jira REST API.
        
        Args:
            attachment_ids: List of attachment IDs
        
        Returns:
            List of attachment data dictionaries
        """
        logger.info(f"Fetching {len(attachment_ids)} attachments...")
        
        attachments = []
        
        for attachment_id in tqdm(attachment_ids, desc="Fetching attachments"):
            try:
                # Get attachment metadata from Jira REST API
                attachment_data = self.client.get_jira_rest_api(
                    f"/rest/api/3/attachment/{attachment_id}"
                )
                attachments.append(attachment_data)
            except Exception as e:
                logger.error(f"Failed to fetch attachment {attachment_id}: {e}")
                # Continue with other attachments
        
        logger.info(f"Fetched {len(attachments)} attachments")
        return attachments
