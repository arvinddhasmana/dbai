# Supply-chain penalty and waiver demo runbook

This runbook exercises contract evidence from the attached Bronze, Silver, and
Gold vendor agreements with structured vendor data from
`globalmart.supply_chain.dim_vendors`. It does not modify the contract files,
contract chunks, or AI Search index.

## Contract facts and expected policy

| Vendor | Contract metadata | Standard late charge | Weather treatment | Boss approval path |
|---|---|---|---|---|
| `VEND-123` — Pacific Warehousing | Bronze, West | 10% of shipment invoice per late day | No weather exemption; the supplier retains 100% liability even for weather, acts of God, or infrastructure shutdowns. | No manager approval under the default `Gold,Platinum` approval-tier configuration. |
| `VEND-456` — Alpine Apparel Ltd | Silver, Northeast | $500 per container per day after the scheduled delivery window | Qualifying severe Northeast weather caps total supplier liability at $1,500 per shipment. Supplier bears the first $1,500; GlobalMart covers additional delay. Notice and event details are required within four hours. Ordinary congestion, missed appointments, staffing/scheduling problems, equipment shortages, and incorrect paperwork do not qualify. | No manager approval under the default configuration. |
| `VEND-789` — Logistics & Electronics Corp | Gold, Midwest | 5% of total shipment invoice per day when delivery exceeds four business days from order placement. | Qualifying severe winter weather, blizzards, or freezing conditions fully waive daily late penalties when an authority identifies materially unsafe travel or a designated route is closed. The supplier must notify GlobalMart within four hours and provide route, shipment, location, revised estimate, and mitigation details. Ordinary rain, traffic, staffing shortages, equipment failure, and poor planning do not qualify. | Manager approval is required because Gold is in the default `APPROVAL_REQUIRED_SUPPORT_TIERS=Gold,Platinum`. |

Manager approval is Boss App policy based on the current `support_tier`; it is
separate from the contract's financial terms. The agreement files do not
provide actual shipment invoice values, container counts, or delay durations.
The numerical values below are explicit test assumptions, not contract facts.
Use only the three listed vendor IDs for evidence tests; the generated
`VEND-321` Platinum row has no matching contract among the attached files.

## 1. Prepare and inspect the test data

Run these scripts in a non-production Databricks SQL warehouse with permission
to update `globalmart.supply_chain.dim_vendors` and create a scoped backup
table in `globalmart.supply_chain`.

1. Run [00_inspect_penalty_scenario_vendors.sql](../../scripts/demo/00_inspect_penalty_scenario_vendors.sql).
   Confirm the three expected vendor IDs exist and review their current rows.
2. Run [01_prepare_penalty_scenario_vendors.sql](../../scripts/demo/01_prepare_penalty_scenario_vendors.sql).
   It snapshots the current values once in
   `globalmart.supply_chain.demo_penalty_vendor_backup`, then aligns the
   vendor name, tier, region, and account manager with the attached contracts.
   Stop if either `assert_true` check fails.
3. Rerun the inspection query. Expected fixture values:

   | Vendor ID | Name | Tier | Region |
   |---|---|---|---|
   | `VEND-123` | Pacific Warehousing | Bronze | West |
   | `VEND-456` | Alpine Apparel Ltd | Silver | Northeast |
   | `VEND-789` | Logistics & Electronics Corp | Gold | Midwest |

The vendor seed values are also defined in
[generate_mock_data.py](../../scripts/deployable/generate_mock_data.py). The
setup script changes only those four columns and never inserts vendors.
It does not update vector-index metadata; the canonical fixture values match
the attached contract metadata. Do not use this script against production or
leave the fixture values in place if they differ from the backed-up values you
intend to preserve.

Before the Boss App demo, confirm its `APPROVAL_REQUIRED_SUPPORT_TIERS`
contains `Gold` (default: `Gold,Platinum`), its Jira issue types are configured,
and the App has working access to the specialist, SQL warehouse, Lakebase, and
Atlassian MCP Service.

## 2. Run the scenarios

Open the deployed Boss App and use the vendor IDs below. First ask for a
contract assessment without requesting an action; this should create no Jira
item. To exercise an action, explicitly ask the Boss App to create a
penalty-tracking item. Review the exact proposal and reply `yes` only when you
intend to proceed. The App must not create an item before that confirmation.
For an action, the specialist must return a verifiable amount and currency
(for these examples, explicitly state `USD`), vendor identity, and cited
contract evidence. The current evidence validator requires these values and
quotes to be present in the specialist answer. If it cannot verify them, the
Boss App should block the action rather than infer missing evidence.

### Scenario A — Bronze: weather does not waive the penalty

Ask:

> For VEND-123, assume a USD 20,000 shipment invoice delivered two days late
> during a declared severe-weather route closure. What does the contract say
> about the penalty and waiver?

Expected evidence: 10% per late day and no weather exemption. Under the stated
test assumption, a simple non-compounded calculation is
`$20,000 × 10% × 2 = $4,000`. The contract makes the supplier fully liable.
The contract does not define a waiver or a cap for this event.

For the action path, ask the App to create a penalty-tracking item for
`VEND-123`, including those test assumptions. After reviewing and confirming
the proposal, expect one `VendorPenalty` item and no manager-approval item
under the default tier configuration.

### Scenario B — Silver: ordinary delay versus qualifying weather cap

Assume four containers are two days late and all amounts are USD. The standard
charge is `4 × 2 × USD 500 = USD 4,000`.

**Qualifying weather:** Ask about a declared severe Northeast winter storm
with the contract's four-hour notice and required shipment/location/ETA
details. Expected result: the supplier's total liability is capped at $1,500
per shipment; GlobalMart covers the amount above the cap. Do not describe this
as a full waiver.

**Non-qualifying event:** Repeat the assessment for ordinary seasonal
congestion, a missed appointment, or an equipment shortage. Expected result:
the cap does not apply, so the stated test assumptions leave the $4,000
standard charge uncapped.

If you explicitly request an action for a penalty-applicable case and confirm
the proposal, expect one `VendorPenalty` item and no manager-approval item.
The Silver agreement's $1,500 weather cap is not an approval threshold.

### Scenario C — Gold: threshold and manager-approval gate

**Threshold check:** Ask about delivery exactly four business days after
order placement. Expected result: the clause applies only when delivery
*exceeds* four business days, so exactly four days does not cross the stated
threshold.

**Penalty and approval:** Ask:

> For VEND-789, assume a USD 10,000 shipment invoice delivered six business days
> after order placement, with no qualifying weather event. Calculate the
> contract penalty and tell me whether manager approval is required.

Under the explicit assumption that the two days beyond the four-business-day
threshold are chargeable, the illustrative amount is
`USD 10,000 × 5% × 2 = USD 1,000`. Gold is approval-required by the default Boss
configuration. Ask the App to create the penalty-tracking item; after user
confirmation, expect a `Request` approval issue and a paused graph. Approve or
reject through the Boss App approval page. A rejection must create no
`VendorPenalty`; an approval should create a separate `VendorPenalty` item.

### Scenario D — Gold: eligible waiver versus ordinary weather

**Eligible waiver:** Ask about severe Midwest winter weather with an
authoritative unsafe-travel/route-closure condition and complete, timely
four-hour notice. Expected result: the daily late penalty is fully waived.
No penalty item should be created because there is no applicable penalty to
track.

**Not eligible:** Repeat for ordinary rain, traffic, staffing shortages,
equipment failure, or poor planning. Expected result: those events do not
qualify for the weather waiver. The standard Gold penalty may apply, and any
confirmed action must still pass the Gold manager-approval gate.

## 3. Negative and safety checks

- Ask only for contract information: no Jira item should be created.
- Request an action but do not confirm the exact proposal: no Jira item
  should be created.
- Reject the user confirmation: no Jira item should be created.
- Ask for a waiver but omit required event facts: the App should state what
  evidence is missing rather than assume the waiver applies.
- If vendor identity, invoice value, duration, currency, or other required
  penalty evidence is absent, expect the action to block rather than invent a
  value.
- For Gold, stop at the approval page until a manager decision is recorded;
  creating a Jira `Request` is not itself approval.
- Jira items are internal tracking artifacts. They do not notify or charge the
  vendor, and they do not execute a contract deduction.

## 4. Restore the original vendor rows

After the demo, run
[02_restore_penalty_scenario_vendors.sql](../../scripts/demo/02_restore_penalty_scenario_vendors.sql).
It restores the backed-up name, tier, region, and account manager values,
verifies the restoration, and then drops the scoped backup table. Confirm the
result with the read-only inspection script.

Do not run the restore script unless the setup script created the backup
table. If a vendor row was deleted during the demo, restore it through the
normal data-management process before running the restore script; the reset
script intentionally updates existing rows only.
