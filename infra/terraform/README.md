# Infrastructure: the cloud demo

Terraform for the public demo on Azure: Container Apps (API, worker, tool gateway, GKN, Regulus), Postgres Flexible Server, a container registry, Key Vault, Log Analytics and a monthly budget. Why it looks like this, and what it costs (about $28 a month), is in [ADR 0009](../../docs/adr/0009-deployment-target.md); why Terraform is in [ADR 0010](../../docs/adr/0010-infrastructure-as-code.md).

The demo replays recorded model responses. Nothing here holds a model key, so the cloud environment cannot spend tokens.

**Status:** written and checked with `terraform validate` and the local tests; not yet deployed.

## What you need

- An Azure subscription **in your own name**. Never use an employer's.
- [Terraform](https://developer.hashicorp.com/terraform/install) 1.9 or later and the [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli).
- About 30 minutes. Postgres takes the longest to create.

## Deploy, step by step

1. **Sign in to Azure.**

   ```bash
   az login
   ```

2. **Write your settings** in `infra/terraform/terraform.tfvars`. Git ignores this file.

   ```hcl
   subscription_id = "<your subscription id>"
   budget_emails   = ["<where budget alerts go>"]
   ```

3. **Create the registry first.** The apps can't start until their images exist.

   ```bash
   cd infra/terraform
   terraform init
   terraform apply -target=azurerm_container_registry.this
   ```

4. **Build the images inside Azure.** Run this from the repository root.

   ```bash
   scripts/cloud_images.sh "$(terraform -chdir=infra/terraform output -raw registry)"
   ```

5. **Create everything else.** Read the plan before typing `yes`.

   ```bash
   terraform -chdir=infra/terraform apply
   ```

6. **Check it.** The role keys are in Key Vault. Each secret has the form `name:key`; the check script wants only the part after the colon.

   ```bash
   cd infra/terraform
   vault=$(terraform output -raw key_vault)
   key() { az keyvault secret show --vault-name "$vault" --name "$1" --query value -o tsv | cut -d: -f2; }
   export SUBMITTER_KEY=$(key submitter-api-keys) REVIEWER_KEY=$(key reviewer-api-keys) AUDITOR_KEY=$(key auditor-api-keys)
   cd ../..
   uv run python scripts/check_week9.py "$(terraform -chdir=infra/terraform output -raw api_url)"
   ```

   Add `--check-cap` once to confirm the daily cap. It uses up the rest of the day's jobs.

## Updating the platform

After the first deploy, `.github/workflows/deploy.yml` (run it from the Actions tab) builds a new platform image and rolls it out to the api, worker and tool-gateway apps. It needs an Azure identity that GitHub can sign in as, through OpenID Connect, and five repository variables. Until those exist it skips. Terraform ignores image changes on those three apps, so `terraform apply` doesn't roll them back.

## Tearing it down

```bash
terraform -chdir=infra/terraform destroy
```

This removes everything, including the database and Key Vault. The demo holds only synthetic data.

## Costs to watch

- **The budget.** It emails at 50% and 80% of the monthly budget (`monthly_budget`, default $30) and when the forecast passes 100%. It does not stop anything.
- **No private endpoints, and no maintenance window, on the Container Apps environment.** Either adds a flat management fee of about $73 a month to a Consumption environment.
- **GKN runs one replica all month** (about $6). The other apps scale to zero.
- **Logs stop at 0.5 GB a day** (`log_daily_quota_gb`). The first 5 GB a month are free.

## Not yet verified in Azure

These three can only be confirmed by the first real deployment:

- **The worker's KEDA rule reaches the private database.**
- **The gateway can write its log to the file share.** It runs as a non-root user.
- **GKN, Regulus and the gateway accept the Host header that Container Apps sends.** `MCP_ALLOWED_HOSTS` lists both the app name and the app's internal domain name.

If one fails, the app's log in Log Analytics says which.
