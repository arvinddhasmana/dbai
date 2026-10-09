"""Deterministic vendor policy lookup using the configured SQL warehouse."""

import asyncio
import re
import time

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementParameterListItem

from agent_server.config import Settings
from agent_server.models import VendorPolicy


class VendorPolicyLookup:
    def __init__(
        self,
        settings: Settings,
        workspace_client: WorkspaceClient,
    ) -> None:
        self._settings = settings
        self._workspace_client = workspace_client

    async def lookup(self, vendor_id: str) -> VendorPolicy:
        if not self._settings.sql_warehouse_id:
            raise RuntimeError("DATABRICKS_SQL_WAREHOUSE_ID is not configured.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", vendor_id):
            raise ValueError("Invalid vendor identifier.")
        return await asyncio.to_thread(self._lookup_sync, vendor_id)

    def _lookup_sync(self, vendor_id: str) -> VendorPolicy:
        response = self._workspace_client.statement_execution.execute_statement(
            statement=(
                "SELECT vendor_id, vendor_name, support_tier "
                "FROM globalmart.supply_chain.dim_vendors "
                "WHERE vendor_id = :vendor_id"
            ),
            warehouse_id=self._settings.sql_warehouse_id,
            parameters=[
                StatementParameterListItem(
                    name="vendor_id", value=vendor_id, type="STRING"
                )
            ],
            wait_timeout="30s",
            row_limit=2,
        )

        def statement_state(result: object) -> str:
            status = getattr(result, "status", None)
            value = getattr(status, "state", None)
            return getattr(value, "value", str(value).rsplit(".", 1)[-1])

        deadline = time.monotonic() + 90
        while statement_state(response) in {"PENDING", "RUNNING"}:
            if time.monotonic() >= deadline:
                raise TimeoutError("Vendor policy lookup exceeded its 90-second limit.")
            time.sleep(1)
            response = self._workspace_client.statement_execution.get_statement(
                response.statement_id
            )
        final_state = statement_state(response)
        if final_state != "SUCCEEDED":
            error = getattr(getattr(response, "status", None), "error", None)
            message = getattr(error, "message", None) or str(error) or "unknown SQL error"
            raise RuntimeError(f"Vendor policy lookup failed ({final_state}): {message}")

        columns = [
            getattr(column, "name", "")
            for column in (
                getattr(getattr(getattr(response, "manifest", None), "schema", None), "columns", None)
                or []
            )
        ]
        rows = getattr(getattr(response, "result", None), "data_array", None) or []
        if not rows:
            raise LookupError(f"Vendor {vendor_id!r} was not found.")
        if len(rows) != 1:
            raise RuntimeError(f"Vendor {vendor_id!r} matched multiple records.")
        record = dict(zip(columns, rows[0], strict=False))
        if not record.get("support_tier"):
            raise RuntimeError("The vendor record has no support_tier.")
        support_tier = record["support_tier"]
        if not isinstance(support_tier, str):
            raise RuntimeError("The vendor record has an invalid support_tier.")
        normalized_support_tier = support_tier.strip()
        if not normalized_support_tier:
            raise RuntimeError("The vendor record has no support_tier.")
        return VendorPolicy.model_validate(
            {
                **record,
                "support_tier": normalized_support_tier,
                "approval_required": self._settings.requires_manager_approval(
                    normalized_support_tier
                ),
            }
        )
