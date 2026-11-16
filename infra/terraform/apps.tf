# The Container Apps environment and the five apps. Only the API is public. The others are
# internal, reached by name inside the environment, as the services are in docker compose.

resource "azurerm_container_app_environment" "this" {
  name                       = "cae-${local.name}"
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  log_analytics_workspace_id = azurerm_log_analytics_workspace.this.id
  infrastructure_subnet_id   = azurerm_subnet.apps.id
  tags                       = local.tags

  # Consumption only: billed per second while running, nothing when scaled to zero. Adding a
  # Dedicated profile would add a flat management fee.
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

resource "azurerm_container_app_environment_storage" "gateway_log" {
  name                         = "gateway-log"
  container_app_environment_id = azurerm_container_app_environment.this.id
  account_name                 = azurerm_storage_account.this.name
  share_name                   = azurerm_storage_share.gateway_log.name
  access_key                   = azurerm_storage_account.this.primary_access_key
  access_mode                  = "ReadWrite"
}

locals {
  env_id = azurerm_container_app_environment.this.id
  domain = azurerm_container_app_environment.this.default_domain
  # The MCP services check the Host header. Inside the environment an app is reached by its
  # name or by its internal FQDN.
  allowed_hosts = { for app in ["tool-gateway", "gkn-mcp", "regulus-mcp"] :
    app => join(",", [app, "${app}:*", "${app}.internal.${local.domain}", "localhost:*", "127.0.0.1:*"])
  }
  platform_image = "${local.images}/axiom-platform:${var.platform_image_tag}"
}

# --- api: the only public app ---------------------------------------------------------------

resource "azurerm_container_app" "api" {
  name                         = "api"
  container_app_environment_id = local.env_id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  dynamic "secret" {
    for_each = toset(["database-url", "submitter-api-keys", "reviewer-api-keys", "auditor-api-keys"])
    content {
      name                = secret.key
      identity            = azurerm_user_assigned_identity.apps.id
      key_vault_secret_id = azurerm_key_vault_secret.this[secret.key].versionless_id
    }
  }

  ingress {
    external_enabled = true
    target_port      = 8000
    transport        = "auto"
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0
    max_replicas = 2

    container {
      name   = "api"
      image  = local.platform_image
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "AXIOM_MODE"
        value = "demo"
      }
      env {
        name  = "AXIOM_API_HOST"
        value = "0.0.0.0"
      }
      env {
        name  = "AXIOM_DAILY_JOB_CAP"
        value = tostring(var.daily_job_cap)
      }
      env {
        name  = "TOOL_GATEWAY_URL"
        value = "http://tool-gateway/mcp"
      }
      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name        = "SUBMITTER_API_KEYS"
        secret_name = "submitter-api-keys"
      }
      env {
        name        = "REVIEWER_API_KEYS"
        secret_name = "reviewer-api-keys"
      }
      env {
        name        = "AUDITOR_API_KEYS"
        secret_name = "auditor-api-keys"
      }

      liveness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/v1/health"
      }
      readiness_probe {
        transport = "HTTP"
        port      = 8000
        path      = "/v1/ready"
      }
    }

    http_scale_rule {
      name                = "http"
      concurrent_requests = "20"
    }
  }

  # An app can pull an image and read a secret only after these exist.
  depends_on = [azurerm_role_assignment.apps_pull, azurerm_key_vault_secret.this]

  lifecycle {
    # The deploy workflow rolls out new image tags; Terraform sets only the first one.
    ignore_changes = [template[0].container[0].image]
  }
}

# --- worker: no ingress; KEDA starts it when there is work in the jobs table -----------------

resource "azurerm_container_app" "worker" {
  name                         = "worker"
  container_app_environment_id = local.env_id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  dynamic "secret" {
    for_each = toset(["database-url", "tool-gateway-tokens"])
    content {
      name                = secret.key
      identity            = azurerm_user_assigned_identity.apps.id
      key_vault_secret_id = azurerm_key_vault_secret.this[secret.key].versionless_id
    }
  }

  template {
    min_replicas = 0
    max_replicas = 1

    container {
      name    = "worker"
      image   = local.platform_image
      command = ["python", "-m", "axiom.jobs.worker"]
      cpu     = 0.5
      memory  = "1Gi"

      env {
        name  = "AXIOM_MODE"
        value = "demo"
      }
      env {
        name  = "TOOL_GATEWAY_URL"
        value = "http://tool-gateway/mcp"
      }
      env {
        name        = "DATABASE_URL"
        secret_name = "database-url"
      }
      env {
        name        = "TOOL_GATEWAY_TOKENS"
        secret_name = "tool-gateway-tokens"
      }
    }

    # The same statuses the worker claims (JobStore.claim_next): new jobs, and jobs a
    # reviewer sent back or approved.
    custom_scale_rule {
      name             = "jobs-waiting"
      custom_rule_type = "postgresql"
      metadata = {
        query                      = "SELECT count(*) FROM jobs WHERE status IN ('queued', 'revising', 'finalizing')"
        targetQueryValue           = "1"
        activationTargetQueryValue = "0"
      }
      authentication {
        secret_name       = "database-url"
        trigger_parameter = "connection"
      }
    }
  }

  # An app can pull an image and read a secret only after these exist.
  depends_on = [azurerm_role_assignment.apps_pull, azurerm_key_vault_secret.this]

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}

# --- tool-gateway: the only route to the tool services (ADR 0014) ----------------------------

resource "azurerm_container_app" "tool_gateway" {
  name                         = "tool-gateway"
  container_app_environment_id = local.env_id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  dynamic "secret" {
    for_each = toset([
      "tool-gateway-tokens",
      "tool-gateway-operator-token",
      "gkn-mcp-secret",
      "regulus-mcp-secret",
    ])
    content {
      name                = secret.key
      identity            = azurerm_user_assigned_identity.apps.id
      key_vault_secret_id = azurerm_key_vault_secret.this[secret.key].versionless_id
    }
  }

  ingress {
    external_enabled           = false
    target_port                = 8003
    transport                  = "http"
    allow_insecure_connections = true
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0
    # One writer for the hash-chained log.
    max_replicas = 1

    volume {
      name         = "gateway-log"
      storage_type = "AzureFile"
      storage_name = azurerm_container_app_environment_storage.gateway_log.name
    }

    container {
      name    = "tool-gateway"
      image   = local.platform_image
      command = ["python", "-m", "axiom.gateway.service"]
      cpu     = 0.25
      memory  = "0.5Gi"

      volume_mounts {
        name = "gateway-log"
        path = "/var/lib/axiom-gateway"
      }

      env {
        name  = "GKN_MCP_URL"
        value = "http://gkn-mcp/mcp"
      }
      env {
        name  = "REGULUS_MCP_URL"
        value = "http://regulus-mcp/mcp"
      }
      env {
        name  = "MCP_ALLOWED_HOSTS"
        value = local.allowed_hosts["tool-gateway"]
      }
      env {
        name        = "TOOL_GATEWAY_TOKENS"
        secret_name = "tool-gateway-tokens"
      }
      env {
        name        = "TOOL_GATEWAY_OPERATOR_TOKEN"
        secret_name = "tool-gateway-operator-token"
      }
      env {
        name        = "GKN_MCP_SECRET"
        secret_name = "gkn-mcp-secret"
      }
      env {
        name        = "REGULUS_MCP_SECRET"
        secret_name = "regulus-mcp-secret"
      }

      liveness_probe {
        transport = "HTTP"
        port      = 8003
        path      = "/healthz"
      }
    }
  }

  # An app can pull an image and read a secret only after these exist.
  depends_on = [azurerm_role_assignment.apps_pull, azurerm_key_vault_secret.this]

  lifecycle {
    ignore_changes = [template[0].container[0].image]
  }
}

# --- gkn-mcp: evidence retrieval. Always one replica: its index lives in the container -------

resource "azurerm_container_app" "gkn" {
  name                         = "gkn-mcp"
  container_app_environment_id = local.env_id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  secret {
    name                = "gkn-mcp-secret"
    identity            = azurerm_user_assigned_identity.apps.id
    key_vault_secret_id = azurerm_key_vault_secret.this["gkn-mcp-secret"].versionless_id
  }

  ingress {
    external_enabled           = false
    target_port                = 8002
    transport                  = "http"
    allow_insecure_connections = true
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    # GKN keeps each job's indexed documents in SQLite inside the container. Scaling to zero
    # would drop them between a draft and a reviewer's "revise" (ADR 0009).
    min_replicas = 1
    max_replicas = 1

    container {
      name   = "gkn-mcp"
      image  = "${local.images}/gkn-cloud:${var.gkn_image_tag}"
      cpu    = 0.25
      memory = "0.5Gi"

      env {
        name  = "GKN_REFERENCE_DIR"
        value = "/references"
      }
      env {
        name  = "MCP_ALLOWED_HOSTS"
        value = local.allowed_hosts["gkn-mcp"]
      }
      env {
        name        = "MCP_SHARED_SECRET"
        secret_name = "gkn-mcp-secret"
      }

      liveness_probe {
        transport = "HTTP"
        port      = 8002
        path      = "/healthz"
      }
    }
  }

  # An app can pull an image and read a secret only after these exist.
  depends_on = [azurerm_role_assignment.apps_pull, azurerm_key_vault_secret.this]
}

# --- regulus-mcp: AI governance provisions; stateless, so it scales to zero -----------------

resource "azurerm_container_app" "regulus" {
  name                         = "regulus-mcp"
  container_app_environment_id = local.env_id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.apps.id]
  }

  registry {
    server   = azurerm_container_registry.this.login_server
    identity = azurerm_user_assigned_identity.apps.id
  }

  secret {
    name                = "regulus-mcp-secret"
    identity            = azurerm_user_assigned_identity.apps.id
    key_vault_secret_id = azurerm_key_vault_secret.this["regulus-mcp-secret"].versionless_id
  }

  ingress {
    external_enabled           = false
    target_port                = 8001
    transport                  = "http"
    allow_insecure_connections = true
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = 0
    max_replicas = 1

    container {
      name   = "regulus-mcp"
      image  = "${local.images}/regulus-mcp:${var.regulus_image_tag}"
      cpu    = 0.5
      memory = "1Gi"

      env {
        name  = "MCP_ALLOWED_HOSTS"
        value = local.allowed_hosts["regulus-mcp"]
      }
      env {
        name        = "MCP_SHARED_SECRET"
        secret_name = "regulus-mcp-secret"
      }

      liveness_probe {
        transport = "HTTP"
        port      = 8001
        path      = "/healthz"
      }
    }
  }

  # An app can pull an image and read a secret only after these exist.
  depends_on = [azurerm_role_assignment.apps_pull, azurerm_key_vault_secret.this]
}
