"""Authentication helpers for local development and Databricks Apps."""

from databricks.sdk import WorkspaceClient
from mlflow.genai.agent_server import get_request_headers


def get_user_workspace_client():
    """Return an on-behalf-of client when the request carries a user token."""
    try:
        token = get_request_headers().get("x-forwarded-access-token")
    except Exception:
        token = None
    if token:
        return WorkspaceClient(token=token, auth_type="pat")
    return WorkspaceClient()
