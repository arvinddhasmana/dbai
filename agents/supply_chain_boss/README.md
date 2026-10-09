# GlobalMart Supply Chain Boss App

This App demonstrates a LangGraph workflow that calls the existing read-only
`agent-supply-chain-sup-dev` Responses API, checks vendor policy, and creates
Jira work items after separate user confirmation and manager approval for
configured support tiers.

## Local development

```bash
uv sync
APPROVAL_REQUIRED_SUPPORT_TIERS="Gold,Platinum" \
  BOSS_CHECKPOINT_BACKEND=memory uv run start-server
```

The in-memory checkpoint backend is for local development only. It does not
survive restarts. Production deployments must use the configured Lakebase
checkpoint branch. The approval-tier environment variable is required at
startup; the bundle supplies its configured default for deployed Apps.

## Databricks configuration

Deploy with the `dbai-dev` profile and configure the Jira assignee emails,
Jira site URL, and MLflow experiment. Approval links use the trusted incoming
App URL, so no `BOSS_APP_BASE_URL` or `DATABRICKS_APP_URL` setting is needed.
Project `KAN`,
approval type `Request`, penalty type `VendorPenalty`, the development SQL
warehouse, the existing supervisor App name, and the approval-required support
tiers (`Gold,Platinum` by default) have defaults in `databricks.yml`.

For example, provide assignees without committing their addresses:

```bash
export BUNDLE_VAR_jira_approver_email="<approver Jira email>"
export BUNDLE_VAR_jira_penalty_assignee_email="<penalty assignee Jira email>"
export BUNDLE_VAR_approval_required_support_tiers="Gold,Platinum"
databricks bundle deploy --target dev
```

The Boss App's configured App resources grant its service principal access to
the SQL warehouse, Lakebase, model endpoint, Specialist App, inventory Genie
space, vendor contract Vector Search index, vendor policy table, and MLflow
trace tables. The Boss requests `ai-gateway`, `sql`, `genie`,
`model-serving`, and `vector-search` user API scopes. These scopes are separate
from App resource grants: the Genie and index resources grant the Boss service
principal `CAN_RUN` and `SELECT`, respectively, while the trace spans and
annotations/logs/metrics resources grant `MODIFY` and the vendor policy table
resource grants `SELECT`. Deploying these resources requires the bundle
deployer to have permission to grant those privileges.
The async checkpoint pool is opened during App startup, kept open while requests
are served, and closed during shutdown. It health-checks pooled connections so
stale connections after Lakebase autoscaling suspension are replaced before
LangGraph uses them.
The Boss calls `/api/responses` using the App's service-principal authorization.
Per-request user OBO forwarding to the specialist is still a follow-up.
The Boss-to-Specialist data path therefore uses the Boss service principal's
resource grants, rather than the signed-in user's data permissions.

The Atlassian MCP Service URL defaults to
`{workspace-host}/ai-gateway/mcp-services/system.ai.atlassian`. The App
requests the `ai-gateway` scope and invokes the MCP Service with the
authenticated user's forwarded Databricks token, not the App service principal.
The user's Atlassian identity must be connected and have permission to create
and assign the configured Jira issue types in project `KAN`.

The App resolves `JIRA_APPROVER_EMAIL` and `JIRA_PENALTY_ASSIGNEE_EMAIL`
through Jira account lookup; each must resolve unambiguously to an assignable
Jira account. The configured approval issue type is `Request`, assigned to the
approver. Vendors whose `support_tier` is in
`APPROVAL_REQUIRED_SUPPORT_TIERS` create a separate `VendorPenalty` issue only
after approval, assigned to the configured penalty assignee. Other tiers create
one `VendorPenalty` issue after user confirmation.

Jira items are internal tracking records. Creating one does not notify the
vendor, charge the vendor, or execute the contract penalty. The previous
`vendor_email` table column, if present, is not read or required by this Jira
workflow.

The vendor policy query expects these fields on
`globalmart.supply_chain.dim_vendors`:

- `vendor_id`
- `vendor_name`
- `support_tier`

`APPROVAL_REQUIRED_SUPPORT_TIERS` is a comma-separated, case-insensitive list
of support tiers that require manager approval; it defaults to `Gold,Platinum`.
The App fails closed when required approval has not been recorded. No vendor
email field or recipient is needed for Jira item creation.

## Security and operational follow-ups

- Forward the original user's OAuth token to the child Responses API only after
  confirming supported audience and scopes and testing end-to-end OBO behavior.
- Restrict approval decisions to the authorized manager role and store a
  verified approver identity.
- Replace broad app-user approval access, protect approval links against
  leakage/CSRF, and define expiry and audit-retention behavior.
- Persist Jira issue keys in a durable action ledger and reconcile uncertain
  issue-creation outcomes before retries; `createJiraIssue` has no idempotency
  key parameter.
- Configure Jira workflow transitions for approval/rejection and confirm that
  assignment notifications reach the configured user.
- Review Jira project visibility and minimize sensitive contract evidence in
  work-item descriptions.
- Verify per-request user OBO to the specialist and remove anonymous
  cross-user conversation fallback.
