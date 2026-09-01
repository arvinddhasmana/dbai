#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPOSITORY_ROOT"

environment_name="${DBAI_ENVIRONMENT:-demo}"
target="${DBAI_BUNDLE_TARGET:-$environment_name}"
state_dir="${DBAI_STATE_DIR:-.dbai-state}"
profile_override=""
confirmed=false
dry_run=false

usage() {
  cat <<'EOF'
Usage: scripts/local/cleanup_local_databricks.sh [options]

Remove local state for one disposable dbai environment without touching other
Databricks profiles or Azure resources.

Options:
  --environment NAME     Environment name (default: DBAI_ENVIRONMENT or demo)
  --target NAME          Bundle target (default: DBAI_BUNDLE_TARGET or environment)
  --profile NAME         Additional profile to remove; must start with dbai-
  --yes                  Confirm profile deletion and cache cleanup
  --dry-run              Show what would be removed without changing anything
  --help                 Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --environment)
      environment_name="$2"
      shift 2
      ;;
    --target)
      target="$2"
      shift 2
      ;;
    --profile)
      profile_override="$2"
      shift 2
      ;;
    --yes)
      confirmed=true
      shift
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$dry_run" != true && "$confirmed" != true ]]; then
  printf '%s\n' 'Pass --yes to remove local profiles and state, or use --dry-run.' >&2
  exit 2
fi

profiles=("dbai-${environment_name}" "dbai-${environment_name}-admin")
if [[ -n "$profile_override" ]]; then
  if [[ ! "$profile_override" =~ ^dbai-[A-Za-z0-9_-]+$ ]]; then
    printf '%s\n' '--profile must be a dedicated dbai-* profile.' >&2
    exit 1
  fi
  profiles+=("$profile_override")
fi

if [[ -d ".databricks/bundle/$target" ]]; then
  backup_dir="/tmp/dbai-bundle-${target}-$(date +%Y%m%d%H%M%S)"
  printf 'Bundle cache: %s -> %s\n' ".databricks/bundle/$target" "$backup_dir"
  if [[ "$dry_run" != true ]]; then
    mv ".databricks/bundle/$target" "$backup_dir"
  fi
fi

if [[ -d "$state_dir" ]]; then
  state_backup="/tmp/dbai-state-${environment_name}-$(date +%Y%m%d%H%M%S)"
  state_files=(
    "${environment_name}-catalog"
    "${environment_name}-sql-warehouse-id"
  )
  for state_file in "${state_files[@]}"; do
    if [[ -f "$state_dir/$state_file" ]]; then
      printf 'State cache: %s -> %s\n' "$state_dir/$state_file" "$state_backup/$state_file"
      if [[ "$dry_run" != true ]]; then
        mkdir -p "$state_backup"
        mv "$state_dir/$state_file" "$state_backup/"
      fi
    fi
  done
fi

for profile in "${profiles[@]}"; do
  if [[ "$dry_run" == true ]]; then
    printf 'Databricks profile: %s\n' "$profile"
    continue
  fi
  if databricks auth describe --profile "$profile" --output json >/dev/null 2>&1; then
    databricks auth logout "$profile" --delete --auto-approve
    printf 'Removed Databricks profile: %s\n' "$profile"
  else
    printf 'Databricks profile already absent: %s\n' "$profile"
  fi
done

printf '%s\n' 'Local Databricks cleanup complete. The main config file and unrelated profiles were preserved.'
