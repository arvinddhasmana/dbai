# GlobalMart Supply Chain Boss App

This App demonstrates a LangGraph workflow that calls the existing read-only
`agent-supply-chain-sup-dev` Responses API, checks vendor policy, and creates
Jira work items after separate user confirmation and (for Platinum vendors)
manager approval.

## Local development

```bash
uv sync
BOSS_CHECKPOINT_BACKEND=memory uv run start-server
```

The in-memory checkpoint backend is for local development only. It does not
survive restarts. Production deployments must use the configured Lakebase
checkpoint branch.

## Databricks configuration

Deploy with the `dbai-dev` profile and configure the Jira assignee emails,
Jira site URL, MLflow experiment, and public Boss App URL. Project `KAN`,
approval type `Request`, penalty type `VendorPenalty`, the development SQL
warehouse, and the existing supervisor App name have defaults in
`databricks.yml`.

For example, provide assignees without committing their addresses:

```bash
export BUNDLE_VAR_jira_approver_email="<approver Jira email>"
export BUNDLE_VAR_jira_penalty_assignee_email="<penalty assignee Jira email>"
databricks bundle deploy --target dev
```

The Boss App's configured App resources grant its service principal access to
the SQL warehouse, Lakebase, model endpoint, and specialist App. The specialist
App must be running and must grant the Boss service principal `CAN_USE`; the
App-to-App resource in `boss.resources.yml` establishes this grant at deployment.
The Boss calls `/api/responses` using the App's service-principal authorization.
Per-request user OBO forwarding to the specialist is still a follow-up.

The Atlassian MCP Service URL defaults to
`{workspace-host}/ai-gateway/mcp-services/system.ai.atlassian`; set
`JIRA_MCP_URL` only to override it. The App requests the `ai-gateway` user
scope and invokes the MCP Service with the authenticated user's forwarded
Databricks token, not the App service principal. The user's Atlassian identity
must be connected and have permission to create and assign the configured Jira
issue types in project `KAN`.

The App resolves `JIRA_APPROVER_EMAIL` and `JIRA_PENALTY_ASSIGNEE_EMAIL`
through Jira account lookup; each must resolve unambiguously to an assignable
Jira account. The configured approval issue type is `Request`, assigned to the
approver. A Platinum penalty creates a separate `VendorPenalty` issue only
after approval, assigned to the configured penalty assignee. A non-Platinum
penalty creates one `VendorPenalty` issue after user confirmation.

Jira items are internal tracking records. Creating one does not notify the
vendor, charge the vendor, or execute the contract penalty. The previous
`vendor_email` table column, if present, is not read or required by this Jira
workflow.

The vendor policy query expects these fields on
`globalmart.supply_chain.dim_vendors`:

- `vendor_id`
- `vendor_name`
- `support_tier`

`support_tier = 'Platinum'` requires manager approval. The App fails closed if
approval is required for Platinum vendors. No vendor email field or recipient
is needed for Jira item creation.

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
