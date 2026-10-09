"""Runtime configuration for the supply-chain Boss App."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    specialist_app_name: str
    model_endpoint: str
    approval_required_support_tiers: frozenset[str]
    jira_project_key: str
    jira_approval_issue_type_name: str
    jira_penalty_issue_type_name: str
    jira_approver_email: str
    jira_penalty_assignee_email: str
    jira_base_url: str
    sql_warehouse_id: str
    jira_mcp_url: str
    lakebase_branch: str
    lakebase_endpoint: str | None
    checkpoint_backend: str

    def requires_manager_approval(self, support_tier: str) -> bool:
        return support_tier.strip().casefold() in self.approval_required_support_tiers


def _parse_approval_required_support_tiers(value: str) -> frozenset[str]:
    tiers = [tier.strip().casefold() for tier in value.split(",")]
    if not tiers or any(not tier for tier in tiers):
        raise ValueError(
            "APPROVAL_REQUIRED_SUPPORT_TIERS must be a comma-separated list "
            "of non-empty support tiers."
        )
    return frozenset(tiers)


def get_settings() -> Settings:
    return Settings(
        specialist_app_name=os.getenv("SPECIALIST_APP_NAME", ""),
        model_endpoint=os.getenv(
            "MODEL_ENDPOINT", "databricks-meta-llama-3-3-70b-instruct"
        ),
        approval_required_support_tiers=_parse_approval_required_support_tiers(
            os.getenv("APPROVAL_REQUIRED_SUPPORT_TIERS", "")
        ),
        jira_project_key=os.getenv("JIRA_PROJECT_KEY", "KAN"),
        jira_approval_issue_type_name=os.getenv(
            "JIRA_APPROVAL_ISSUE_TYPE_NAME", "Request"
        ),
        jira_penalty_issue_type_name=os.getenv(
            "JIRA_PENALTY_ISSUE_TYPE_NAME", "VendorPenalty"
        ),
        jira_approver_email=os.getenv("JIRA_APPROVER_EMAIL", ""),
        jira_penalty_assignee_email=os.getenv("JIRA_PENALTY_ASSIGNEE_EMAIL", ""),
        jira_base_url=os.getenv(
            "JIRA_BASE_URL", "https://arvinddhasmana.atlassian.net"
        ),
        sql_warehouse_id=os.getenv("DATABRICKS_SQL_WAREHOUSE_ID", ""),
        jira_mcp_url=os.getenv("JIRA_MCP_URL", ""),
        lakebase_branch=os.getenv("LAKEBASE_AUTOSCALING_BRANCH", ""),
        lakebase_endpoint=os.getenv("LAKEBASE_AUTOSCALING_ENDPOINT") or None,
        checkpoint_backend=os.getenv("BOSS_CHECKPOINT_BACKEND", "lakebase"),
    )
