#!/usr/bin/env bash
# One-time Azure setup for GitHub Actions deployments (run after `az login`).
#
# Creates the resource group, an Entra app registration that GitHub Actions can sign in as
# via OIDC (no stored Azure password), and gives it Contributor on that resource group only.
# Prints the values to save as GitHub repository variables. Safe to re-run.
set -euo pipefail

REPO="${REPO:-BojanSapunov/garmin_mcp}"
RG="${RG:-rg-training-log}"
LOCATION="${LOCATION:-germanywestcentral}"
APP_NAME="${APP_NAME:-github-${REPO//\//-}-deploy}"

SUBSCRIPTION_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
echo "Subscription: $(az account show --query name -o tsv)"

echo "Registering resource providers (first time can take a few minutes)..."
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.DBforPostgreSQL Microsoft.Consumption; do
  az provider register --namespace "$ns" --wait >/dev/null
done

echo "Resource group $RG in $LOCATION..."
az group create --name "$RG" --location "$LOCATION" --output none

echo "App registration $APP_NAME..."
CLIENT_ID=$(az ad app list --display-name "$APP_NAME" --query "[0].appId" -o tsv)
if [ -z "$CLIENT_ID" ]; then
  CLIENT_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
fi
az ad sp show --id "$CLIENT_ID" >/dev/null 2>&1 || az ad sp create --id "$CLIENT_ID" --output none

# Trust GitHub Actions jobs that run in the "production" environment of this repo, and nothing else.
SUBJECT="repo:${REPO}:environment:production"
if ! az ad app federated-credential list --id "$CLIENT_ID" --query "[?subject=='$SUBJECT']" -o tsv | grep -q .; then
  az ad app federated-credential create --id "$CLIENT_ID" --parameters "{
    \"name\": \"github-production\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"$SUBJECT\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }" --output none
fi

echo "Contributor role on $RG only..."
SCOPE="/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RG"
for attempt in 1 2 3 4 5; do  # a new service principal can take a moment to become visible
  if az role assignment create --assignee "$CLIENT_ID" --role Contributor --scope "$SCOPE" --output none 2>/dev/null \
     || az role assignment list --assignee "$CLIENT_ID" --scope "$SCOPE" --query "[0]" -o tsv | grep -q .; then
    break
  fi
  sleep 10
done

cat <<EOF

Done. Add these as GitHub repository *variables* (Settings > Secrets and variables > Actions > Variables):
  AZURE_CLIENT_ID=$CLIENT_ID
  AZURE_TENANT_ID=$TENANT_ID
  AZURE_SUBSCRIPTION_ID=$SUBSCRIPTION_ID
  AZURE_RESOURCE_GROUP=$RG
EOF
