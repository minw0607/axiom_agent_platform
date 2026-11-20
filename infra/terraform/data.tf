# Postgres, Key Vault and the generated secrets, and the file share for the gateway's log.

# --- Postgres: the smallest burstable server, reachable only inside the network -----------

resource "azurerm_private_dns_zone" "db" {
  name                = "${local.name}.private.postgres.database.azure.com"
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "db" {
  name                  = "db-link"
  private_dns_zone_name = azurerm_private_dns_zone.db.name
  resource_group_name   = azurerm_resource_group.this.name
  virtual_network_id    = azurerm_virtual_network.this.id
}

resource "random_password" "db" {
  length  = 40
  special = false
}

resource "azurerm_postgresql_flexible_server" "this" {
  name                          = "psql-${local.name}"
  location                      = azurerm_resource_group.this.location
  resource_group_name           = azurerm_resource_group.this.name
  version                       = "17"
  sku_name                      = "B_Standard_B1ms"
  storage_mb                    = 32768
  backup_retention_days         = 7
  geo_redundant_backup_enabled  = false
  delegated_subnet_id           = azurerm_subnet.db.id
  private_dns_zone_id           = azurerm_private_dns_zone.db.id
  public_network_access_enabled = false
  administrator_login           = "axiom"
  administrator_password        = random_password.db.result
  tags                          = local.tags

  depends_on = [azurerm_private_dns_zone_virtual_network_link.db]

  lifecycle {
    # Azure picks a zone at creation; don't fight it on later plans.
    ignore_changes = [zone]
  }
}

resource "azurerm_postgresql_flexible_server_database" "axiom" {
  name      = "axiom"
  server_id = azurerm_postgresql_flexible_server.this.id
  charset   = "UTF8"
  collation = "en_US.utf8"
}

# --- Generated secrets: role keys, gateway tokens, service secrets -----------------------

locals {
  # The same names and shapes docker compose's demo-keys service generates (src/axiom/demo_keys.py).
  roles = { submitter = "rm-demo", reviewer = "co-demo", auditor = "audit-demo" }
  # The workflow steps that call tools, as <app_id>.<step> (config/tool_gateway.yaml).
  gateway_steps = [
    "credit_memo.intake",
    "credit_memo.drafter",
    "credit_memo.compliance",
    "credit_memo.finalize",
    "model_review.provisions",
  ]
  service_secrets = [
    "gkn-mcp-secret",
    "regulus-mcp-secret",
    "tool-gateway-operator-token",
  ]
  secret_names = concat(
    ["database-url", "tool-gateway-tokens"],
    [for role in keys(local.roles) : "${role}-api-keys"],
    local.service_secrets,
  )
  secret_values = merge(
    {
      "database-url" = format(
        "postgresql://axiom:%s@%s:5432/axiom?sslmode=require",
        random_password.db.result,
        azurerm_postgresql_flexible_server.this.fqdn,
      )
      "tool-gateway-tokens" = join(",", [
        for step in local.gateway_steps : "${step}:${random_password.step[step].result}"
      ])
    },
    { for role, principal in local.roles :
    "${role}-api-keys" => "${principal}:${random_password.role[role].result}" },
    { for name in local.service_secrets : name => random_password.service[name].result },
  )
}

resource "random_password" "role" {
  for_each = local.roles
  length   = 48
  special  = false
}

resource "random_password" "step" {
  for_each = toset(local.gateway_steps)
  length   = 48
  special  = false
}

resource "random_password" "service" {
  for_each = toset(local.service_secrets)
  length   = 64
  special  = false
}

resource "azurerm_key_vault" "this" {
  name                       = "kv-${local.name}"
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  soft_delete_retention_days = 7
  purge_protection_enabled   = false
  tags                       = local.tags
}

# Whoever runs Terraform writes the secrets; the apps only read them.
resource "azurerm_role_assignment" "deployer_secrets" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "apps_secrets" {
  scope                = azurerm_key_vault.this.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.apps.principal_id
}

# Role assignments take a minute to apply; writing secrets before then is refused.
resource "time_sleep" "rbac" {
  create_duration = "90s"
  depends_on      = [azurerm_role_assignment.deployer_secrets, azurerm_role_assignment.apps_secrets]
}

resource "azurerm_key_vault_secret" "this" {
  for_each     = toset(local.secret_names)
  name         = each.key
  value        = local.secret_values[each.key]
  key_vault_id = azurerm_key_vault.this.id
  depends_on   = [time_sleep.rbac]
}

# --- The tool gateway's hash-chained call log: a small file share, so it outlives restarts --

resource "azurerm_storage_account" "this" {
  name                            = "${var.prefix}st${random_string.suffix.result}"
  location                        = azurerm_resource_group.this.location
  resource_group_name             = azurerm_resource_group.this.name
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = local.tags
}

resource "azurerm_storage_share" "gateway_log" {
  name               = "gateway-log"
  storage_account_id = azurerm_storage_account.this.id
  quota              = 1
}
