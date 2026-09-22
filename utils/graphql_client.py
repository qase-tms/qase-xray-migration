"""GraphQL client with authentication, retry logic, and rate limiting for Xray Cloud API."""

import time
import json
import base64
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .logger import get_logger

logger = get_logger(__name__)


class GraphQLClient:
    """
    GraphQL client for Xray Cloud API with authentication, retry logic, and rate limiting.
    
    Features:
    - Bearer token authentication with automatic refresh
    - Exponential backoff retry logic
    - Rate limit handling (300 requests per 5 minutes)
    - GraphQL error parsing and handling
    """
    
    AUTH_URL = "https://xray.cloud.getxray.app/api/v2/authenticate"
    GRAPHQL_URL = "https://xray.cloud.getxray.app/api/v2/graphql"
    
    # Rate limits: 300 requests per 5 minutes (Standard plan)
    RATE_LIMIT_REQUESTS = 300
    RATE_LIMIT_WINDOW = 300  # 5 minutes in seconds
    
    def __init__(self, client_id: str, client_secret: str, jira_url: str, 
                 jira_email: Optional[str] = None, jira_api_token: Optional[str] = None,
                 jira_oauth_client_id: Optional[str] = None, jira_oauth_client_secret: Optional[str] = None):
        """
        Initialize the GraphQL client.
        
        Args:
            client_id: Xray Cloud client ID
            client_secret: Xray Cloud client secret
            jira_url: Jira instance URL (e.g., https://yourcompany.atlassian.net)
            jira_email: Optional Jira email for Basic Auth (for attachment downloads)
            jira_api_token: Optional Jira API token for Basic Auth (for attachment downloads)
            jira_oauth_client_id: Optional Jira OAuth 2.0 client ID (for attachment downloads)
            jira_oauth_client_secret: Optional Jira OAuth 2.0 client secret (for attachment downloads)
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.jira_url = jira_url.rstrip('/')
        self.jira_email = jira_email
        self.jira_api_token = jira_api_token
        self.jira_oauth_client_id = jira_oauth_client_id
        self.jira_oauth_client_secret = jira_oauth_client_secret
        self.token: Optional[str] = None
        self.token_expires_at: Optional[datetime] = None
        self.jira_oauth_token: Optional[str] = None
        self.jira_oauth_token_expires_at: Optional[datetime] = None
        
        # Rate limiting tracking
        self.request_timestamps: list = []
        
        # Setup session with retry strategy
        self.session = requests.Session()
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["POST", "GET"]
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
    
    def _get_jira_basic_auth_header(self) -> Optional[str]:
        """
        Get Basic Auth header for Jira REST API if credentials are available.
        
        Returns:
            Basic Auth header string or None if credentials not available
        """
        if self.jira_email and self.jira_api_token:
            credentials = f"{self.jira_email}:{self.jira_api_token}"
            encoded = base64.b64encode(credentials.encode()).decode()
            return f"Basic {encoded}"
        return None
    
    def authenticate(self) -> str:
        """
        Authenticate with Xray Cloud and get Bearer token.
        
        Returns:
            Bearer token string
        
        Raises:
            Exception: If authentication fails
        """
        logger.info("Authenticating with Xray Cloud...")
        
        try:
            response = self.session.post(
                self.AUTH_URL,
                json={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret
                },
                timeout=30
            )
            response.raise_for_status()
            
            # Handle both JSON response {"token": "..."} and plain string token
            data = response.json()
            if isinstance(data, str):
                # Response is a plain string token
                token = data
            elif isinstance(data, dict):
                # Response is a JSON object with token field
                token = data.get("token")
            else:
                raise ValueError(f"Unexpected authentication response format: {type(data)}")
            
            if not token:
                raise ValueError("No token received from authentication endpoint")
            
            # Tokens typically expire after 1 hour, but we'll refresh every 50 minutes to be safe
            self.token = token
            self.token_expires_at = datetime.now() + timedelta(minutes=50)
            
            logger.info("Authentication successful")
            return token
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Authentication failed: {e}")
            raise Exception(f"Failed to authenticate with Xray Cloud: {e}")
    
    def _ensure_authenticated(self):
        """Ensure we have a valid token, refresh if needed."""
        if not self.token or (self.token_expires_at and datetime.now() >= self.token_expires_at):
            self.authenticate()
    
    def _check_rate_limit(self):
        """Check and enforce rate limits."""
        now = time.time()
        
        # Remove timestamps older than the rate limit window
        self.request_timestamps = [
            ts for ts in self.request_timestamps
            if now - ts < self.RATE_LIMIT_WINDOW
        ]
        
        # If we're at the limit, wait until the oldest request expires
        if len(self.request_timestamps) >= self.RATE_LIMIT_REQUESTS:
            oldest_timestamp = min(self.request_timestamps)
            wait_time = self.RATE_LIMIT_WINDOW - (now - oldest_timestamp) + 1
            logger.warning(f"Rate limit reached. Waiting {wait_time:.1f} seconds...")
            time.sleep(wait_time)
            # Clean up again after waiting
            self.request_timestamps = [
                ts for ts in self.request_timestamps
                if time.time() - ts < self.RATE_LIMIT_WINDOW
            ]
        
        # Record this request
        self.request_timestamps.append(time.time())
    
    def execute_query(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        max_retries: int = 3
    ) -> Dict[str, Any]:
        """
        Execute a GraphQL query with retry logic.
        
        Args:
            query: GraphQL query string
            variables: Optional query variables
            max_retries: Maximum number of retry attempts
        
        Returns:
            Response data dictionary
        
        Raises:
            Exception: If query execution fails after retries
        """
        self._ensure_authenticated()
        self._check_rate_limit()
        
        payload = {
            "query": query,
            "variables": variables or {}
        }
        
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }
        
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                logger.debug(f"Executing GraphQL query (attempt {attempt + 1}/{max_retries})")
                
                response = self.session.post(
                    self.GRAPHQL_URL,
                    json=payload,
                    headers=headers,
                    timeout=60
                )
                
                # Handle rate limiting (429)
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 60))
                    logger.warning(f"Rate limited. Waiting {retry_after} seconds...")
                    time.sleep(retry_after)
                    continue
                
                response.raise_for_status()
                
                # Parse response - handle both JSON dict and string responses
                try:
                    data = response.json()
                except ValueError:
                    # Response might be a plain string
                    response_text = response.text
                    logger.error(f"GraphQL response is not JSON: {response_text[:200]}")
                    raise Exception(f"GraphQL response is not valid JSON: {response_text[:200]}")
                
                # Check if data is a dict
                if not isinstance(data, dict):
                    logger.error(f"GraphQL response is not a dict: {type(data)}, value: {str(data)[:200]}")
                    raise Exception(f"GraphQL response is not a dictionary: {type(data)}")
                
                # Check for GraphQL errors
                if "errors" in data:
                    errors = data["errors"]
                    error_messages = [err.get("message", str(err)) if isinstance(err, dict) else str(err) for err in errors]
                    raise Exception(f"GraphQL errors: {', '.join(error_messages)}")
                
                # Return data field
                return data.get("data", {})
                
            except requests.exceptions.RequestException as e:
                last_exception = e
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff
                    logger.warning(f"Request failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Request failed after {max_retries} attempts: {e}")
        
        raise Exception(f"Failed to execute GraphQL query after {max_retries} attempts: {last_exception}")
    
    def get_jira_rest_api(self, endpoint: str, params: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Make a request to Jira REST API (for projects, attachments, etc.).
        
        Tries authentication methods in order: Basic Auth, OAuth 2.0, Xray Bearer token.
        
        Args:
            endpoint: API endpoint (e.g., "/rest/api/3/project")
            params: Optional query parameters
        
        Returns:
            Response data
        """
        self._check_rate_limit()
        
        url = f"{self.jira_url}{endpoint}"
        headers = {
            "Accept": "application/json"
        }
        
        # Jira Cloud REST API requires Basic Auth (email + API token)
        # OAuth 2.0 client credentials flow doesn't work - Jira requires authorization code flow (user interaction)
        basic_auth = self._get_jira_basic_auth_header()
        
        if basic_auth:
            headers["Authorization"] = basic_auth
        else:
            # Fallback to Xray Bearer token (may work for some endpoints, but not attachments)
            self._ensure_authenticated()
            headers["Authorization"] = f"Bearer {self.token}"
        
        try:
            response = self.session.get(url, headers=headers, params=params, timeout=30)
            response.raise_for_status()
            
            # Handle both JSON and text responses
            content_type = response.headers.get('Content-Type', '')
            try:
                data = response.json()
                if not isinstance(data, dict):
                    logger.warning(f"Response is not a dict, it's {type(data)}: {str(data)[:200]}")
                    return {"raw_response": str(data)}
                return data
            except ValueError as e:
                logger.warning(f"Response is not JSON. Content-Type: {content_type}, Text: {response.text[:200]}")
                return {"error": response.text, "status_code": response.status_code, "content_type": content_type}
        except requests.exceptions.RequestException as e:
            logger.error(f"Jira REST API request failed: {e}")
            if hasattr(e, 'response') and e.response is not None:
                logger.error(f"Response status: {e.response.status_code}")
                logger.error(f"Response text: {e.response.text[:500]}")
            raise Exception(f"Failed to fetch from Jira REST API: {e}")
    
    def test_jira_auth(self) -> bool:
        """
        Test if Jira authentication credentials are working.
        For scoped tokens (e.g., read:attachment:jira), we skip the /myself test
        since scoped tokens may not have user profile read permissions.
        We'll verify authentication works when actually downloading attachments.
        
        Returns:
            True if credentials are present, False otherwise
        """
        basic_auth = self._get_jira_basic_auth_header()
        if not basic_auth:
            logger.error(f"Basic Auth header not created - email: {bool(self.jira_email)}, token: {bool(self.jira_api_token)}")
            logger.error("Jira authentication failed - Basic Auth (email + API token) is required")
            return False
        
        # For scoped tokens (read:attachment:jira), /myself endpoint may not be accessible
        # So we'll just verify credentials are present and test will happen during actual attachment download
        logger.info("Jira credentials present - will verify during attachment download")
        logger.info("Note: Scoped tokens may not have permission to access /myself endpoint")
        logger.info("Authentication will be verified when downloading attachments")
        return True
    
    def check_attachment_access(self, attachment_id: str) -> Dict[str, Any]:
        """
        Check if we can access attachment metadata (to verify permissions).
        
        Args:
            attachment_id: Attachment ID
            
        Returns:
            Attachment metadata dictionary or empty dict if access denied
        """
        url = f"{self.jira_url}/rest/api/3/attachment/{attachment_id}"
        headers = {"Accept": "application/json"}
        
        # Try authentication methods in order: Basic Auth, Xray Bearer token
        # Note: OAuth 2.0 client credentials flow doesn't work with Jira Cloud
        basic_auth = self._get_jira_basic_auth_header()
        
        if basic_auth:
            headers["Authorization"] = basic_auth
        else:
            # Fallback to Xray Bearer token
            self._ensure_authenticated()
            headers["Authorization"] = f"Bearer {self.token}"
        
        try:
            response = self.session.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                return response.json()
            return {}
        except Exception:
            return {}
    
    def _get_jira_oauth_token(self) -> Optional[str]:
        """
        Get OAuth 2.0 access token for Jira REST API if OAuth credentials are available.
        
        Returns:
            OAuth access token or None if credentials not available
        """
        if not self.jira_oauth_client_id or not self.jira_oauth_client_secret:
            return None
        
        # Check if we have a valid token
        if self.jira_oauth_token and self.jira_oauth_token_expires_at:
            if datetime.now() < self.jira_oauth_token_expires_at:
                return self.jira_oauth_token
        
        # Request new token using OAuth 2.0 client credentials flow
        try:
            logger.info("Authenticating with Jira OAuth 2.0...")
            token_url = f"{self.jira_url}/rest/oauth2/latest/token"
            
            auth_header = base64.b64encode(
                f"{self.jira_oauth_client_id}:{self.jira_oauth_client_secret}".encode()
            ).decode()
            
            headers = {
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            data = {
                "grant_type": "client_credentials"
            }
            
            response = self.session.post(token_url, headers=headers, data=data, timeout=30)
            response.raise_for_status()
            
            token_data = response.json()
            self.jira_oauth_token = token_data.get("access_token")
            expires_in = token_data.get("expires_in", 3600)  # Default to 1 hour
            
            if self.jira_oauth_token:
                self.jira_oauth_token_expires_at = datetime.now() + timedelta(seconds=expires_in - 60)  # Refresh 1 min early
                logger.info("Jira OAuth 2.0 authentication successful")
                return self.jira_oauth_token
            else:
                logger.error("No access token received from Jira OAuth endpoint")
                return None
                
        except Exception as e:
            logger.error(f"Jira OAuth 2.0 authentication failed: {e}")
            return None
    
    def download_attachment(self, content_url: str) -> bytes:
        """
        Download an attachment file from Jira REST API.
        
        Tries authentication methods in order: Basic Auth, OAuth 2.0, Xray Bearer token.
        
        Args:
            content_url: Full URL to attachment content (e.g., "https://.../rest/api/3/attachment/content/10100")
        
        Returns:
            File content as bytes
        
        Raises:
            Exception: If download fails
        """
        self._check_rate_limit()
        
        headers = {
            "Accept": "*/*"  # Accept any content type for binary files
        }
        
        # Jira Cloud REST API requires Basic Auth (email + API token) for attachment downloads
        # OAuth 2.0 client credentials flow doesn't work - Jira requires authorization code flow (user interaction)
        basic_auth = self._get_jira_basic_auth_header()
        
        if basic_auth:
            headers["Authorization"] = basic_auth
        else:
            # Fallback to Xray Bearer token (typically won't work for attachments)
            self._ensure_authenticated()
            headers["Authorization"] = f"Bearer {self.token}"
        
        try:
            response = self.session.get(content_url, headers=headers, timeout=60, stream=True)
            response.raise_for_status()
            content = response.content
            return content
            
        except requests.exceptions.RequestException as e:
            error_msg = f"Failed to download attachment from {content_url}: {e}"
            logger.error(error_msg)
            
            if hasattr(e, 'response') and e.response is not None:
                status_code = e.response.status_code
                logger.error(f"Response status: {status_code}")
                
                if status_code == 401:
                    if not basic_auth:
                        logger.error("401 Unauthorized - Jira REST API requires Basic Auth")
                        logger.error("Please add 'jira_email' and 'jira_api_token' to config.json")
                        logger.error("Note: OAuth 2.0 doesn't work for Jira Cloud REST API (requires user interaction)")
                    else:
                        logger.error("401 Unauthorized - Check that your Jira email and API token are correct")
                
                elif status_code == 403:
                    logger.error("403 Forbidden - Your account doesn't have permission to access this attachment")
                    logger.error("Possible causes:")
                    logger.error("  - Scoped token missing required scopes (may need 'read:jira-work' in addition to 'read:attachment:jira')")
                    logger.error("  - Your Jira account doesn't have access to the project/issue")
                    logger.error("  - The attachment is restricted/private")
                    logger.error("  - Project-level security settings restrict attachment access")
                    logger.error("")
                    logger.error("Troubleshooting:")
                    logger.error("  1. Verify you can access the attachment in Jira web interface")
                    logger.error("  2. Check if your scoped token needs additional scopes:")
                    logger.error("     - Try adding 'read:jira-work' scope")
                    logger.error("     - Or use a full-permission token to test if it's a scope issue")
                    logger.error("  3. Ensure your account has 'Browse Projects' permission for the project")
                
                elif status_code == 404:
                    logger.error("404 Not Found - Attachment may have been deleted or moved")
            
            raise Exception(error_msg)

    def download_xray_cloud_attachment(self, url: str) -> bytes:
        """
        Download a file from Xray Cloud ``/api/v2/attachments/{id}`` (test-run evidence, etc.).

        Uses the same Xray Cloud Bearer token as GraphQL — not Jira Basic Auth.
        """
        self._ensure_authenticated()
        self._check_rate_limit()
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "*/*",
        }
        try:
            response = self.session.get(url, headers=headers, timeout=120)
            response.raise_for_status()
            return response.content
        except requests.exceptions.RequestException as e:
            logger.error("Failed to download Xray Cloud attachment from %s: %s", url, e)
            raise Exception(f"Failed to download Xray Cloud attachment: {e}") from e
