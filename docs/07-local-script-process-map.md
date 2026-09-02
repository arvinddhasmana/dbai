# Local Script Process Map

This document describes the current contents of `scripts/local/` and how the
scripts participate in the repository workflows. The scripts are not all
intended to be run for every deployment. They cover separate administrator,
deployment, demo, validation, and cleanup processes.

## Executive Summary

All 15 local scripts have a distinct role in the current implementation. There
are no obvious duplicate or unused files:

- `configure_github_azure.sh` and `configure_databricks_environment.sh` are
  one-time or occasional administrator setup tools.
- `deploy_infrastructure.sh`, `deploy_workload.sh`, and
  `deploy_app.sh` are deliberately split deployment stages.
- `deploy_demo_environment.sh` is a local convenience orchestrator that runs
  the disposable environment flow end to end.
- The Python files provide bootstrap, validation, data-change, access-control,
  and teardown behavior. `demo_environment.py` is a shared implementation
  module rather than a command-line entry point.
- `cleanup_local_databricks.sh` removes local CLI/cache state only. It does not
  delete Azure resources or Databricks data-plane objects.

## Process Overview

### Administrator and CI authentication setup

```text
configure_github_azure.sh
        |
        v
GitHub Environments + Azure OIDC application, federated credentials, and role
        |
        v
configure_databricks_environment.sh
        |
        +--> grant_data_access.py --prepare-bootstrap
        |
        v
Databricks service-principal assignment, catalog/warehouse setup, permissions
```

`configure_github_azure.sh` prepares Azure and GitHub identity configuration.
`configure_databricks_environment.sh` prepares the corresponding Databricks
account and workspace permissions. These scripts are normally run by an
administrator before the GitHub Actions deployment workflows.

### Staged deployment to an existing workspace

```text
deploy_workload.sh
        |
        v
Databricks Bundle: App, jobs, and resources
        |
        v
bootstrap_demo_environment.py
        |
        +--> demo_environment.py
        +--> grant_data_access.py
        +--> validate_demo_workspace.py
        +--> create_vendor_contract_index.py
        |
        v
Catalog data, contract files, refresh job, AI Search, Genie function, grants
        |
        v
deploy_app.sh
        |
        v
Started App and deployed App revision
```

The staged flow is used by local setup and GitHub Actions. Bootstrap can also
deploy the Bundle itself unless `--skip-deploy` is supplied; the staged process
uses `deploy_workload.sh` first and passes `--skip-deploy` to avoid deploying
the Bundle twice.

### Disposable local Azure environment

```text
deploy_demo_environment.sh
        |
        +--> Azure Bicep deployment in infra/main.bicep
        +--> deploy_workload.sh
        +--> bootstrap_demo_environment.py --skip-deploy
        +--> deploy_app.sh
```

This is the local convenience path for creating a dedicated disposable Azure
resource group and Databricks workspace, then completing the workload setup.
It is not a wrapper around `deploy_infrastructure.sh`; it performs the
infrastructure deployment itself.

The narrower staged alternative is:

```text
deploy_infrastructure.sh --> deploy_workload.sh --> bootstrap_demo_environment.py --> deploy_app.sh
```

That alternative is useful when infrastructure, workload, and bootstrap need
to be run as separate operations, as they are in GitHub Actions.

### Contract change demonstration

```text
run_contract_change_demo.py
        |
        +--> demo_environment.py (upload/delete files)
        +--> databricks bundle run refresh_vendor_contract_chunks
        +--> databricks bundle run upsert_demo_vendor (add action only)
```

The `reset`, `add`, `update`, and `delete` actions mutate only the managed demo
files in the contract Volume and optionally run the corresponding refresh job.
Use `--no-run` to separate the Volume mutation from the job execution.

### Validation and recovery operations

`validate_demo_workspace.py` is the preflight gate used after ingestion and
before AI Search provisioning. It can also be run independently, with
`--require-index` adding an AI Search existence check.

`create_vendor_contract_index.py` is the standalone recovery/provisioning
operation for the AI Search endpoint and triggered Delta Sync index. Bootstrap
calls its `provision_index` function, and validation imports its configured
embedding endpoint constant.

`grant_data_access.py` is both a reusable library and a command-line tool. It
grants SQL and AI Search access to the App and OBO user, and its
`--prepare-bootstrap` mode grants overwrite-related table permissions to job
identities. Bootstrap imports it; the Databricks configuration script invokes
it as a subprocess.

### Teardown and local cleanup

```text
destroy_demo_environment.sh --yes
        |
        v
destroy_demo_environment.py --yes
        |
        +--> AI Search index and endpoint
        +--> Volume contents and named Unity Catalog objects
        +--> Bundle-managed jobs and App
        |
        v
SQL warehouse created by this deployment, if recorded
        |
        v
Deployment-owned Azure resource groups
        |
        +--> cleanup_local_databricks.sh (only with --cleanup-local-config)
```

## File-by-File Mapping

| File | Process role | Used by / entry point | What it does |
| --- | --- | --- | --- |
| [`configure_github_azure.sh`](../scripts/local/configure_github_azure.sh) | Administrator setup | Run manually before GitHub Actions | Creates or reuses the Entra app and service principal, grants the Azure role, creates or migrates GitHub OIDC federated credentials, creates GitHub Environments, and writes their Azure settings and optional demo variables. |
| [`configure_databricks_environment.sh`](../scripts/local/configure_databricks_environment.sh) | Administrator setup | Run manually after the Azure workspace exists | Registers and assigns the GitHub deployment service principal in Databricks, verifies workspace entitlements, discovers or creates the catalog and SQL warehouse, grants catalog/warehouse/bootstrap permissions, optionally grants model and AI Search permissions, and writes Databricks variables to the selected GitHub Environment. |
| [`deploy_infrastructure.sh`](../scripts/local/deploy_infrastructure.sh) | Infrastructure stage | Deploy Infrastructure workflow; staged local setup | Builds and deploys `infra/main.bicep` at subscription scope, creating the primary and managed resource groups and Databricks workspace, then prints the workspace URL. |
| [`deploy_workload.sh`](../scripts/local/deploy_workload.sh) | Workload stage | Deploy Workload workflow; staged local setup | Resolves the workspace and authentication mode, validates the Databricks Bundle, deploys App/jobs/resources with the configured catalog, warehouse, model endpoint, and AI Search endpoint, and recovers Bundle state by binding an existing App when necessary. |
| [`deploy_demo_environment.sh`](../scripts/local/deploy_demo_environment.sh) | End-to-end disposable environment | Run manually for a complete local Azure demo | Deploys the disposable Azure infrastructure, then calls the workload, bootstrap, and App activation stages with the selected workspace, catalog, and SQL warehouse configuration. |
| [`bootstrap_demo_environment.py`](../scripts/local/bootstrap_demo_environment.py) | Data-plane bootstrap | Bootstrap Databricks Environment workflow; local staged flow; disposable orchestrator | Creates the catalog/schema/Volume when needed, grants bootstrap access, optionally deploys the Bundle, runs structured-data and contract-ingestion jobs, uploads baseline contracts, validates prerequisites, provisions AI Search, creates the Genie SQL function, and grants App/OBO access. |
| [`deploy_app.sh`](../scripts/local/deploy_app.sh) | App activation stage | Bootstrap Databricks Environment workflow; local staged flow; disposable orchestrator | Locates the Bundle-uploaded App source, starts a stopped Databricks App, waits for it to become ready, and deploys the current App revision with the catalog and warehouse configuration. |
| [`validate_demo_workspace.py`](../scripts/local/validate_demo_workspace.py) | Preflight and recovery gate | Bootstrap; standalone validation; App/workspace troubleshooting | Checks the SQL warehouse, source table type and Delta format, required columns, Change Data Feed, SQL access, embedding endpoint, model endpoint, and optionally the AI Search index. |
| [`create_vendor_contract_index.py`](../scripts/local/create_vendor_contract_index.py) | AI Search provisioning | Bootstrap imports `provision_index`; standalone recovery command | Idempotently creates the configured AI Search endpoint and a triggered Delta Sync index over the contract-chunk source table using the configured embedding endpoint. |
| [`grant_data_access.py`](../scripts/local/grant_data_access.py) | Permissions | Imported by bootstrap; called by Databricks configuration; standalone access repair | Grants least-privilege catalog, schema, table, function, and AI Search endpoint access to the App and signed-in OBO user. Its bootstrap mode grants `SELECT, MODIFY` on existing ingestion tables to job identities. |
| [`demo_environment.py`](../scripts/local/demo_environment.py) | Shared Python module | Imported by bootstrap, teardown, contract demo, index provisioning, validation, and access-related flows | Centralizes environment names, catalog/table/Volume/index constants, Databricks and AI Search client creation, baseline-file discovery, Volume upload/deletion, and SQL execution helpers. |
| [`run_contract_change_demo.py`](../scripts/local/run_contract_change_demo.py) | Incremental ingestion demo | Run manually with `reset`, `add`, `update`, or `delete` | Applies a controlled contract-file change in the Volume, then runs full rebuild or incremental refresh and the vendor upsert job when required. |
| [`destroy_demo_environment.py`](../scripts/local/destroy_demo_environment.py) | Databricks data-plane teardown | Called by the shell wrapper; also run directly | Deletes AI Search objects, Volume contents, named Unity Catalog objects, and optionally Bundle-managed jobs/App or the schema. It does not delete Azure resource groups. |
| [`destroy_demo_environment.sh`](../scripts/local/destroy_demo_environment.sh) | Complete disposable-environment teardown | Run manually for Azure teardown | Authenticates to the workspace, runs the Python data-plane teardown, removes a deployment-created warehouse, deletes the two validated deployment-owned Azure resource groups, and optionally invokes local cleanup. |
| [`cleanup_local_databricks.sh`](../scripts/local/cleanup_local_databricks.sh) | Local state cleanup | Optional final step from destroy wrapper; standalone | Moves the selected Bundle cache and `.dbai-state` files to timestamped `/tmp` backups and removes only dedicated `dbai-*` Databricks CLI profiles. It preserves the main config and unrelated profiles and never touches Azure or Databricks data. |

## Recommended Commands by Situation

| Situation | Command sequence |
| --- | --- |
| Configure GitHub Actions for an environment | `configure_github_azure.sh`, then `configure_databricks_environment.sh` |
| Provision Azure only | `deploy_infrastructure.sh` |
| Deploy code/resources to an existing workspace | `deploy_workload.sh` |
| Populate an existing workspace | `bootstrap_demo_environment.py --skip-deploy`, then `deploy_app.sh` |
| Create a disposable local Azure demo | `deploy_demo_environment.sh` |
| Check readiness | `validate_demo_workspace.py --require-index` |
| Demonstrate a contract change | `run_contract_change_demo.py add|update|delete`, then trigger AI Search sync manually |
| Remove Databricks objects but keep Azure | `destroy_demo_environment.py --yes` |
| Remove the complete disposable environment | `destroy_demo_environment.sh --yes` |
| Remove disposable local CLI/cache state too | `destroy_demo_environment.sh --yes --cleanup-local-config` |

The deployment scripts expect to be run from the repository root, as shown in
the existing documentation and enforced by their repository-root setup.