# Boss Agent: Jira Work-Item Workflow Plan

## Goal and product behavior

The Boss Agent is a separate Databricks App using LangGraph. It calls the
existing `agents/supply_chain_supervisor` through the Responses API for
read-only inventory and contract evidence, applies the deterministic vendor
policy, and creates Jira work items through the built-in
`system.ai.atlassian` MCP Service.

Jira work items replace both former Gmail actions. **Creating an item records
internal work; it does not notify the vendor or execute a charge.** The Boss
Agent must use that precise language in its responses.

Agreed workflow:

- General supply-chain questions use the specialist and create no Jira work
  item.
- A penalty action requires complete specialist evidence, a vendor policy
  lookup, and explicit user confirmation of the exact proposal.
- `support_tier = 'Platinum'` in
  `globalmart.supply_chain.dim_vendors` requires manager approval.
- After user confirmation for Platinum:
  1. Create a Jira approval item assigned to the configured approver. Include
     a minimal evidence summary and a request-specific Boss App approval URL.
  2. Pause the graph at the existing Boss App Approve/Reject page and resume
     only the matching persisted thread after a decision.
  3. Record the decision on the approval item. If approved, create a separate
     penalty-tracking item; if rejected, create no penalty-tracking item.
- After user confirmation for a non-Platinum vendor, create one
  penalty-tracking item without a manager-approval item.
- Assign all penalty-tracking items to the configured Jira assignee.
- Configure the approval assignee and penalty assignee by environment. The
  initial approval account is `arvinddhasmana@gmail.com`; the initial penalty
  assignee is `arvindkumarwork21@gmail.com`. Do not embed those addresses in
  source code or commit them as environment values.
- Use Jira project key `KAN`, approval issue type `Request`, and penalty
  issue type `VendorPenalty`. A live MCP discovery verified these exact names.
- Keep any existing `vendor_email` table column untouched, but do not require
  or query it for the Jira-only workflow.

## Reviewed implementation and workspace state

The existing Boss App under `agents/supply_chain_boss` already has:

- a LangGraph flow, Lakebase checkpoint configuration, and separate
  user-confirmation and manager-approval interrupts;
- a request-specific approval page/API that resumes the saved graph thread;
- a fixed Responses API caller for the read-only specialist;
- structured evidence extraction and a parameterized vendor policy lookup;
- a basic chat page and focused tests.

The Gmail path is coupled into `agent_server/email.py`, workflow state and
nodes, `agent_server/models.py`, `agent_server/config.py`,
`resources/boss.resources.yml`, `databricks.yml`, the README, approval-page
wording, and tests. All of those references need to be migrated or removed.

Workspace checks with the authenticated `dbai-dev` profile verified:

- `system.ai.atlassian` is configured and reachable through
  `/ai-gateway/mcp-services/system.ai.atlassian`;
- `createJiraIssue`, `lookupJiraAccountId`, and
  `addCommentToJiraIssue` are available;
- project `Supply Chain` has key `KAN` and issue types `Request` and
  `VendorPenalty` (as well as `Epic` and `Subtask`);
- a test issue was created/read back as `KAN-1`; the new
  `VendorPenalty` issue type was subsequently verified in the project.

The existing App already requests the `ai-gateway` user API scope and carries
the forwarded Databricks user token only in request-scoped context for MCP
calls. Preserve that pattern for Atlassian. Do not checkpoint or log the
forwarded token.

## Phased implementation

### Phase 1 — Update the plan and runtime configuration

- Replace the former email-centered plan with this Jira workflow and record
  what is implemented versus deferred.
- Replace Gmail configuration with:
  - `JIRA_PROJECT_KEY=KAN`
  - `JIRA_APPROVAL_ISSUE_TYPE_NAME=Request`
  - `JIRA_PENALTY_ISSUE_TYPE_NAME=VendorPenalty`
  - `JIRA_APPROVER_EMAIL`
  - `JIRA_PENALTY_ASSIGNEE_EMAIL`
  - `JIRA_BASE_URL` for user-facing issue links
- Default the MCP URL to the current workspace host plus
  `/ai-gateway/mcp-services/system.ai.atlassian`.
- Keep `ai-gateway` user scope. Document the required permission to invoke the
  MCP Service, the user's Atlassian account connection, and Jira project
  create/assign permission.
- Store actual user addresses in App deployment environment configuration,
  not Python/YAML literals committed to the repository.

### Phase 2 — Implement a narrow Atlassian MCP adapter

- Replace `agent_server/email.py` with a typed Jira adapter that uses the
  current request's user-scoped Databricks client and discovers/validates MCP
  tool schemas.
- Use `lookupJiraAccountId` to resolve each configured email and require one
  unambiguous account result. Use the returned account ID in
  `createJiraIssue.assignee_account_id`.
- Use `createJiraIssue` for the configured project and issue type, and
  `addCommentToJiraIssue` to record an approval decision.
- Validate tool availability, issue type/project configuration, required
  fields, MCP errors, result structure, issue key, and user-facing browse URL.
  Fail explicitly on missing or ambiguous tools/accounts and Jira permission
  errors.
- Call Jira deterministically from graph nodes only after the applicable
  confirmation/approval checks. Do not expose the full Atlassian tool catalog
  to an unconstrained model/tool loop.

### Phase 3 — Rewire the penalty and approval graph

- Remove Gmail settings, `GmailMcpSender`, `EmailMessage`, email-delivery
  state, vendor-recipient checks, and vendor-email lookup requirements.
- Keep structured specialist evidence, quote validation, vendor identity, and
  the deterministic Platinum policy check.
- Add typed approval-item and penalty-item results to graph state, including
  issue keys and URLs needed to reference the two Platinum items.
- For Platinum, create a `Request` approval item only after user confirmation,
  assign it to the configured approver, include the Boss App approval URL, and
  then interrupt at the existing manager decision node.
- Record manager approval/rejection on that Jira item. Rejection terminates
  without creating a penalty item; approval creates a separate
  `VendorPenalty` item assigned to the configured penalty assignee and
  referencing the approval item.
- For non-Platinum, create only a `VendorPenalty` item after user
  confirmation, assigned to the configured penalty assignee.
- Preserve graph checkpointing and resume for the matching conversation.
  Report issue links and accurate statuses; never describe item creation as
  vendor notification or contract execution.

### Phase 4 — Update API responses and user interfaces

- Update the chat and approval pages to show Jira issue links and explain that
  Jira items are internal tracking artifacts.
- Add the Jira approval item link to the approval UI and the
  request-specific Boss App decision URL to the Jira approval item.
- Ensure approval/rejection comments are associated with the correct Jira
  issue and graph request.
- Remove all stale email wording and the unnecessary requirement for a vendor
  email address.

### Phase 5 — Update tests and deployment documentation

- Replace email tests with adapter and workflow tests for:
  - no Jira action on informational requests or before user confirmation;
  - non-Platinum creation of one assigned `VendorPenalty`;
  - Platinum creation/assignment of `Request`, paused approval, recorded
    rejection with no penalty item, and approval followed by a separate
    `VendorPenalty`;
  - account lookup ambiguity/failure, missing tools, malformed responses,
    permission errors, and failure-shaped API responses.
- Update `agents/supply_chain_boss/README.md`, `databricks.yml`, and
  `resources/boss.resources.yml` to remove Gmail setup and describe Jira
  variables, permissions, project/type setup, and the per-request MCP identity.
- Run focused tests, compile/type checks available in the project, and
  `databricks bundle validate --target dev`. Perform another live create only
  when explicitly intended; the test issue `KAN-1` already verified
  connectivity and `VendorPenalty` is now present.

## Implementation status

- The Boss App now uses a request-scoped `system.ai.atlassian` MCP adapter
  instead of Gmail. The graph creates the Platinum approval item, records the
  decision, and creates the separate penalty-tracking item only on approval;
  the non-Platinum path creates one penalty-tracking item after confirmation.
- Vendor-email lookup/gating, Gmail configuration, email models, and
  email-focused tests have been removed. The `vendor_email` source column is
  not changed or required.
- The approval page links to its Jira item, and chat/approval responses show
  the created Jira keys and links.
- The approver and penalty-assignee bundle variables intentionally have no
  committed email defaults. Supply them as `BUNDLE_VAR_jira_approver_email`
  and `BUNDLE_VAR_jira_penalty_assignee_email` at deployment.
- Validation completed: 14 focused tests pass, Python compilation succeeds,
  and `databricks bundle validate --target dev --profile dbai-dev` succeeds.
  No additional live Jira item was created as part of this migration; the
  earlier test issue `KAN-1` remains the live connectivity check.

## Future TODO backlog — recommendations deferred beyond the MVP

1. **Idempotency and uncertain-create recovery:** persist returned Jira keys
   with an external-action ledger/outbox and a stable request correlation
   field. `createJiraIssue` has no idempotency argument; search/reconcile an
   uncertain create before retrying after a timeout or restart, so a resumed
   graph does not create duplicate items.
2. **Manager-only authorization:** verify the authenticated approver's Jira
   identity/role, capture approver identity, and enforce requester/approver
   separation where required.
3. **Approval URL and endpoint protection:** add expiring, single-use
   credentials, CSRF/origin validation, and protection against approval-link
   leakage.
4. **Jira lifecycle configuration:** define dedicated approval/penalty
   workflows, transition mappings, and required custom fields instead of
   relying on generic `Request` and the initial decision comment.
5. **Assignment notifications and permissions:** verify Jira's notification
   scheme actually alerts the assignee; test inactive accounts and review who
   can view approval evidence in the project.
6. **Least-privilege tool surface:** use supported Unity Catalog grants and
   service policies to restrict callers/tools. If the built-in service cannot
   enforce create-only access, evaluate a narrowly scoped custom MCP wrapper.
7. **Evidence minimization and retention:** classify the Jira project's
   visibility, include only necessary contract excerpts/vendor data, and
   define retention and cleanup.
8. **Operational resilience:** add reconciliation for partial failure across
   the two-item Platinum path, retry classification, sanitized metrics/traces,
   audit retention, approval expiry, and escalation.
9. **Identity isolation:** verify per-request OBO to the specialist only after
   validating its token audience/scopes; remove anonymous shared-thread
   fallback and test multi-user isolation.
10. **Dynamic Jira metadata validation:** validate required fields and issue
    type configuration at runtime and avoid hard-coding assumptions about
    project workflows.

## Acceptance criteria

- Informational questions create no Jira items.
- No penalty-related Jira item is created until the user confirms the exact
  evidence-backed proposal.
- Non-Platinum confirmation creates one assigned `VendorPenalty` item.
- Platinum confirmation creates an assigned `Request` approval item and pauses
  the matching graph thread.
- Rejection is recorded and creates no penalty item. Approval is recorded and
  creates a separate assigned `VendorPenalty` item linked to the approval
  item.
- Responses provide the relevant Jira keys/links and do not claim vendor
  notification or penalty execution.
- Jira/MCP errors are surfaced explicitly and cannot appear as successful
  issue creation.
- The existing specialist remains read-only, and user tokens are not stored
  in graph state or logs.
