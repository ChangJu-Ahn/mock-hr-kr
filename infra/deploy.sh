#!/usr/bin/env bash
# Deploy the mock HR system to Azure Container Apps (Consumption).
#
# Prereqs: az CLI logged in. Images must already be published (public) to GHCR
# by the GitHub Actions workflow (.github/workflows/images.yml).
#
# NOTE ON DATA: the SQLite DB lives on an ephemeral EmptyDir volume, so this
# deploy starts a fresh replica and resets the dataset to the seeded snapshot.
# Rows written through REST/MCP since the last start are not carried over.
#
# Usage:
#   ./infra/deploy.sh                      # defaults: rg-mock-hr-kr / koreacentral
#   RG=my-rg LOCATION=eastus ./infra/deploy.sh
set -euo pipefail

RG="${RG:-rg-mock-hr-kr}"
LOCATION="${LOCATION:-koreacentral}"
APP_NAME="${APP_NAME:-mock-hr}"
APP_IMAGE="${APP_IMAGE:-ghcr.io/changju-ahn/mock-hr-app:latest}"
PROXY_IMAGE="${PROXY_IMAGE:-ghcr.io/changju-ahn/mock-hr-proxy:latest}"

echo "==> Ensuring containerapp extension + providers"
az extension add --name containerapp --upgrade --only-show-errors -y >/dev/null 2>&1 || true
az provider register --namespace Microsoft.App --wait --only-show-errors || true
az provider register --namespace Microsoft.OperationalInsights --wait --only-show-errors || true

echo "==> Creating resource group '$RG' in '$LOCATION'"
az group create -n "$RG" -l "$LOCATION" -o none

echo "==> Deploying Bicep"
az deployment group create \
  -g "$RG" \
  -n "mock-hr-$(date +%s)" \
  -f infra/main.bicep \
  -p location="$LOCATION" appName="$APP_NAME" appImage="$APP_IMAGE" proxyImage="$PROXY_IMAGE" \
  -o none

FQDN="$(az containerapp show -g "$RG" -n "$APP_NAME" --query properties.configuration.ingress.fqdn -o tsv)"

echo ""
echo "==> Deployed. Endpoints:"
echo "    Web console : https://$FQDN/"
echo "    REST API    : https://$FQDN/api      (docs: https://$FQDN/api/docs)"
echo "    MCP server  : https://$FQDN/mcp"
