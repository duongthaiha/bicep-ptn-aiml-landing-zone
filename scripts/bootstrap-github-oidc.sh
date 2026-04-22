#!/usr/bin/env bash
# Bootstrap GitHub OIDC + GitHub Environments for the landing zone.
#
# Creates (idempotently):
#   - One Entra app per role: validation (Reader on sandbox RG) + per-env deploy
#     apps (Contributor + User Access Administrator on the env RG).
#   - Federated credentials scoped to:
#       repo:<owner>/<repo>:environment:<env>     for deploy identities
#       repo:<owner>/<repo>:pull_request          for the validation identity
#   - GitHub Environments and per-environment variables (AZURE_CLIENT_ID,
#     AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID, AZURE_LOCATION, etc.).
#
# Requirements: az CLI logged in with permission to create app registrations and
# assign RBAC; gh CLI logged in with admin on the target repo.
#
# Usage:
#   ./scripts/bootstrap-github-oidc.sh \
#       --repo my-org/my-repo \
#       --subscription <sub-id> \
#       --location eastus2 \
#       --envs dev,test,prod \
#       --sandbox-rg rg-ailz-pr-sandbox \
#       --env-rg-prefix rg-ailz-

set -euo pipefail

REPO=""
SUBSCRIPTION=""
LOCATION=""
ENVS="dev,test,prod"
SANDBOX_RG=""
ENV_RG_PREFIX="rg-ailz-"

usage() {
  grep '^#' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --subscription) SUBSCRIPTION="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --envs) ENVS="$2"; shift 2 ;;
    --sandbox-rg) SANDBOX_RG="$2"; shift 2 ;;
    --env-rg-prefix) ENV_RG_PREFIX="$2"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown arg: $1"; usage ;;
  esac
done

[[ -z "$REPO" || -z "$SUBSCRIPTION" || -z "$LOCATION" || -z "$SANDBOX_RG" ]] && usage

OWNER="${REPO%%/*}"
NAME="${REPO##*/}"
TENANT_ID=$(az account show --query tenantId -o tsv)

ensure_app() {
  local app_name="$1"
  local app_id
  app_id=$(az ad app list --display-name "$app_name" --query "[0].appId" -o tsv)
  if [[ -z "$app_id" ]]; then
    app_id=$(az ad app create --display-name "$app_name" --query appId -o tsv)
    az ad sp create --id "$app_id" >/dev/null
  fi
  echo "$app_id"
}

ensure_fic() {
  local app_id="$1" subject="$2" name="$3"
  local existing
  existing=$(az ad app federated-credential list --id "$app_id" --query "[?subject=='$subject'].name" -o tsv)
  if [[ -z "$existing" ]]; then
    az ad app federated-credential create --id "$app_id" --parameters "$(cat <<JSON
{
  "name": "$name",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "$subject",
  "audiences": ["api://AzureADTokenExchange"]
}
JSON
)" >/dev/null
  fi
}

ensure_role() {
  local app_id="$1" scope="$2" role="$3"
  az role assignment create --assignee "$app_id" --role "$role" --scope "$scope" --only-show-errors 2>/dev/null || true
}

ensure_rg() {
  local rg="$1"
  az group show -n "$rg" >/dev/null 2>&1 || az group create -n "$rg" -l "$LOCATION" >/dev/null
}

set_env_var() {
  local env="$1" key="$2" value="$3"
  gh api -X PUT "repos/$REPO/environments/$env/variables/$key" -f name="$key" -f value="$value" >/dev/null 2>&1 \
    || gh api -X POST "repos/$REPO/environments/$env/variables" -f name="$key" -f value="$value" >/dev/null
}

ensure_env() {
  local env="$1"
  gh api -X PUT "repos/$REPO/environments/$env" --input - >/dev/null <<JSON
{}
JSON
}

# --- Validation identity (PR sandbox) ---
echo "==> Validation identity"
VAL_APP_NAME="gh-${NAME}-validation"
VAL_APP_ID=$(ensure_app "$VAL_APP_NAME")
ensure_rg "$SANDBOX_RG"
SANDBOX_SCOPE="/subscriptions/$SUBSCRIPTION/resourceGroups/$SANDBOX_RG"
ensure_role "$VAL_APP_ID" "$SANDBOX_SCOPE" "Reader"
ensure_role "$VAL_APP_ID" "$SANDBOX_SCOPE" "Contributor"  # what-if needs ARM evaluation; Contributor for sandbox is acceptable
ensure_env "pr-sandbox"
ensure_fic "$VAL_APP_ID" "repo:$REPO:environment:pr-sandbox" "pr-sandbox"
set_env_var "pr-sandbox" "AZURE_CLIENT_ID" "$VAL_APP_ID"
set_env_var "pr-sandbox" "AZURE_TENANT_ID" "$TENANT_ID"
set_env_var "pr-sandbox" "AZURE_SUBSCRIPTION_ID" "$SUBSCRIPTION"
set_env_var "pr-sandbox" "AZURE_LOCATION" "$LOCATION"
set_env_var "pr-sandbox" "AZURE_RESOURCE_GROUP" "$SANDBOX_RG"

# --- Per-environment deploy identities ---
IFS=',' read -r -a ENV_ARR <<< "$ENVS"
for env in "${ENV_ARR[@]}"; do
  echo "==> Deploy identity for environment: $env"
  rg="${ENV_RG_PREFIX}${env}"
  ensure_rg "$rg"
  app_name="gh-${NAME}-${env}-deploy"
  app_id=$(ensure_app "$app_name")
  scope="/subscriptions/$SUBSCRIPTION/resourceGroups/$rg"
  ensure_role "$app_id" "$scope" "Contributor"
  ensure_role "$app_id" "$scope" "User Access Administrator"
  ensure_env "$env"
  ensure_fic "$app_id" "repo:$REPO:environment:$env" "$env"
  set_env_var "$env" "AZURE_CLIENT_ID" "$app_id"
  set_env_var "$env" "AZURE_TENANT_ID" "$TENANT_ID"
  set_env_var "$env" "AZURE_SUBSCRIPTION_ID" "$SUBSCRIPTION"
  set_env_var "$env" "AZURE_LOCATION" "$LOCATION"
  set_env_var "$env" "AZURE_RESOURCE_GROUP" "$rg"
done

echo
echo "Done. Configure required reviewers for test/prod in repo Settings → Environments."
