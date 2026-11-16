# gkn-mcp for the cloud demo: the released GKN image plus the two reference collections that
# docker compose mounts as volumes. Container Apps has no bind mounts, so they are baked in.
#   az acr build -r <registry> -t gkn-cloud:<tag> -f docker/gkn-cloud.Dockerfile \
#     --build-arg GKN_IMAGE=<registry>.azurecr.io/gkn-mcp:0.2.0 .
ARG GKN_IMAGE=gkn-mcp:0.2.0
FROM ${GKN_IMAGE}
COPY data/synthetic/credit_policy /references/credit_policy
COPY data/lending_refs /references/lending_refs
ENV GKN_REFERENCE_DIR=/references
