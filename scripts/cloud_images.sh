#!/usr/bin/env bash
# Build the cloud demo's images inside Azure Container Registry (az acr build), so nothing is
# pushed from this machine. Needs the Azure CLI, signed in to the subscription Terraform uses.
#
#   scripts/cloud_images.sh <registry-name> [platform-tag]
#
# The registry name is Terraform's `registry` output. The GKN and Regulus images are built
# from their release tags, the same ones docker-compose.yml pins.
set -euo pipefail

registry="${1:?usage: scripts/cloud_images.sh <registry-name> [platform-tag]}"
tag="${2:-$(git rev-parse --short=12 HEAD)}"
GKN_REF="v0.2.0"
REGULUS_REF="v0.2.1"
cd "$(dirname "$0")/.."

echo "regulus-mcp:${REGULUS_REF#v}"
az acr build --registry "$registry" --image "regulus-mcp:${REGULUS_REF#v}" \
  "https://github.com/minw0607/Regulus.git#${REGULUS_REF}"

echo "gkn-mcp:${GKN_REF#v}"
az acr build --registry "$registry" --image "gkn-mcp:${GKN_REF#v}" \
  "https://github.com/minw0607/geometric_knowledge_network.git#${GKN_REF}"

echo "gkn-cloud:${tag} and :latest"
az acr build --registry "$registry" --image "gkn-cloud:${tag}" --image "gkn-cloud:latest" \
  --file docker/gkn-cloud.Dockerfile \
  --build-arg "GKN_IMAGE=${registry}.azurecr.io/gkn-mcp:${GKN_REF#v}" .

echo "axiom-platform:${tag} and :latest"
az acr build --registry "$registry" --image "axiom-platform:${tag}" --image "axiom-platform:latest" .
