#!/usr/bin/env bash
# One-time Azure setup, run with your own `az login` (needs Owner on the subscription).
#
# Creates:
#   - the resource group
#   - a Key Vault (RBAC) holding the app's secrets, copied from .env without printing them,
#     plus a generated Postgres admin password
#   - a user-assigned managed identity the container app uses to read those secrets
#   - an Entra app registration that GitHub Actions signs in as via OIDC (no stored password),
#     trusted only for this repo's "production" environment, with Contributor on the resource group
# Prints the non-secret values to save as GitHub repository variables. Safe to re-run:
# existing secrets are kept unless FORCE_SECRETS=1.
set -euo pipefail

REPO="${REPO:-BojanSapunov/garmin_mcp}"
RG="${RG:-rg-training-log}"
LOCATION="${LOCATION:-germanywestcentral}"
APP_NAME="${APP_NAME:-github-${REPO//\//-}-deploy}"
IDENTITY="${IDENTITY:-id-training-log}"
ENV_FILE="${ENV_FILE:-.env}"
PYTHON="${PYTHON:-.venv/bin/python}"

SUBSCRIPTION_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
KV="${KV:-kv-tlog-$(printf '%s' "$SUBSCRIPTION_ID/$RG" | shasum | cut -c1-8)}"
ME=$(az ad signed-in-user show --query id -o tsv)
echo "Subscription: $(az account show --query name -o tsv)"

echo "Registering resource providers (first time can take a few minutes)..."
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.DBforPostgreSQL Microsoft.Consumption \
          Microsoft.KeyVault Microsoft.ManagedIdentity; do
  az provider register --namespace "$ns" --wait >/dev/null
done

echo "Resource group $RG in $LOCATION..."
az group create --name "$RG" --location "$LOCATION" --output none
RG_SCOPE="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RG"

assign() {  # assign <principal-object-id> <principal-type> <role> <scope>; retries while new principals propagate
  for _ in 1 2 3 4 5 6; do
    if az role assignment list --assignee "$1" --role "$3" --scope "$4" --query "[0].id" -o tsv | grep -q .; then return 0; fi
    az role assignment create --assignee-object-id "$1" --assignee-principal-type "$2" --role "$3" \
      --scope "$4" --output none 2>/dev/null && return 0
    sleep 10
  done
  echo "Could not assign $3 on $4" >&2; return 1
}

echo "Key Vault $KV..."
if ! az keyvault show --name "$KV" --output none 2>/dev/null; then
  az keyvault create --name "$KV" --resource-group "$RG" --location "$LOCATION" \
    --enable-rbac-authorization true --enabled-for-template-deployment true --output none
fi
KV_ID=$(az keyvault show --name "$KV" --query id -o tsv)
assign "$ME" User "Key Vault Secrets Officer" "$KV_ID"

echo "Managed identity $IDENTITY..."
az identity create --name "$IDENTITY" --resource-group "$RG" --location "$LOCATION" --output none
IDENTITY_PRINCIPAL=$(az identity show --name "$IDENTITY" --resource-group "$RG" --query principalId -o tsv)
assign "$IDENTITY_PRINCIPAL" ServicePrincipal "Key Vault Secrets User" "$KV_ID"

echo "Secrets (values are read from $ENV_FILE and never printed)..."
TMP=$(umask 077 && mktemp -d)
trap 'rm -rf "$TMP"' EXIT
"$PYTHON" - "$ENV_FILE" "$TMP" <<'PY'
import secrets, sys
from pathlib import Path
from dotenv import dotenv_values

env, out = dotenv_values(sys.argv[1]), Path(sys.argv[2])
mapping = {"garmin-api-key": "GARMIN_API_KEY", "google-client-id": "GOOGLE_CLIENT_ID",
           "google-client-secret": "GOOGLE_CLIENT_SECRET", "allowed-email": "ALLOWED_EMAIL",
           "session-secret": "SESSION_SECRET"}
missing = [var for var in mapping.values() if not (env.get(var) or "").strip()]
if missing:
    sys.exit(f"missing in {sys.argv[1]}: {', '.join(missing)}")
for name, var in mapping.items():
    (out / name).write_text(env[var].strip())
# Upper, lower, digit and symbol, as Azure Postgres requires; URL-safe characters only.
(out / "postgres-admin-password").write_text("Pg1-" + secrets.token_urlsafe(32))
PY
for f in "$TMP"/*; do
  name=$(basename "$f")
  for _ in 1 2 3 4 5 6; do  # the Secrets Officer role can take a minute to apply
    if [ "${FORCE_SECRETS:-0}" != "1" ] && az keyvault secret show --vault-name "$KV" --name "$name" --query id -o tsv >/dev/null 2>&1; then
      echo "  $name: exists, kept"; break
    fi
    if az keyvault secret set --vault-name "$KV" --name "$name" --file "$f" --encoding utf-8 --output none 2>/dev/null; then
      echo "  $name: set"; break
    fi
    sleep 10
  done
done

echo "App registration $APP_NAME for GitHub Actions..."
CLIENT_ID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv)
if [ -z "$CLIENT_ID" ]; then
  CLIENT_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
fi
az ad sp show --id "$CLIENT_ID" >/dev/null 2>&1 || az ad sp create --id "$CLIENT_ID" --output none
SP_ID=$(az ad sp show --id "$CLIENT_ID" --query id -o tsv)

SUBJECT="repo:${REPO}:environment:production"
if ! az ad app federated-credential list --id "$CLIENT_ID" --query "[?subject=='$SUBJECT'].name" -o tsv | grep -q .; then
  az ad app federated-credential create --id "$CLIENT_ID" --parameters "{
    \"name\": \"github-production\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"$SUBJECT\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }" --output none
fi
assign "$SP_ID" ServicePrincipal Contributor "$RG_SCOPE"

cat <<EOF

Done. Add these as GitHub repository *variables* (Settings > Secrets and variables > Actions > Variables):
  AZURE_CLIENT_ID=$CLIENT_ID
  AZURE_TENANT_ID=$TENANT_ID
  AZURE_SUBSCRIPTION_ID=$SUBSCRIPTION_ID
  AZURE_RESOURCE_GROUP=$RG
  AZURE_KEY_VAULT=$KV
  BUDGET_EMAIL=<email for cost alerts>
  GARMIN_KEY_EXPIRES=<YYYY-MM-DD>
No GitHub *secrets* are needed.
EOF
