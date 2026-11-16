# ADR 0010: Infrastructure as code

**Status:** Accepted (week 9, Nov 15, 2026). Written; `terraform validate` is the only check run so far.

## Context

The cloud demo (ADR 0009) is about fifteen Azure resources: a resource group, a virtual network with two subnets, Postgres, a private DNS zone, a container registry, a managed identity with three role assignments, Key Vault and its secrets, a storage account and file share, a Log Analytics workspace, a Container Apps environment, five apps and a budget. It must be possible to create it, change it with a reviewed diff, and delete all of it, without clicking through the portal.

## Options considered

| Option | Strengths | Weaknesses |
|---|---|---|
| **Terraform (azurerm provider)** | `plan` shows the change before it happens; one tool across clouds, so the phase 4 kind/Helm work and any other cloud reuse it; generates secrets (`random`) and waits for role assignments (`time`) in the same run | A state file to keep, which holds the generated secrets; the azurerm provider can lag new Azure features |
| Bicep | Azure's own; no state file (Azure is the state); new features on day one | Azure only; `what-if` is less reliable than `plan`; secrets need a separate step or script |

## Decision

**Terraform**, in [`infra/terraform/`](../../infra/terraform/), with the azurerm, random and time providers.

- **Local state, gitignored.** One person deploys the demo, so the state stays on that machine. It holds the database password and the generated keys, so it is treated like a password. A remote backend in a storage account is the step when a second person or CI runs Terraform.
- **Terraform owns the environment; the deploy workflow owns image tags.** Terraform sets each platform app's first image and then ignores later image changes. `.github/workflows/deploy.yml` builds new images in the registry and rolls them out. Without that split, every `terraform apply` would roll the apps back to the tag in its variables.
- **CI signs in to Azure with OpenID Connect.** The deploy workflow uses a federated identity, so no Azure password or key is stored in GitHub. It skips with a notice until its repository variables exist, as the live gates do without model secrets.
- **Images are built in Azure** (`az acr build`, `scripts/cloud_images.sh`), from the same release tags `docker-compose.yml` pins for GKN and Regulus, so nothing is pushed from a laptop.

## Consequences

- `terraform destroy` removes everything, including the Key Vault (purged, not kept for 90 days) and the database. The demo holds only synthetic data and recordings, so nothing is lost that cannot be recreated.
- The first `apply` needs the images in the registry before the apps can start, so setup is in three steps (registry, images, everything else). [`infra/terraform/README.md`](../../infra/terraform/README.md) lists them.
- Generated secrets are in two places: Key Vault, where the apps read them, and the state file. Rotating one means replacing its `random_password` and applying again.
