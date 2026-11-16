output "api_url" {
  description = "The public demo endpoint (HTTPS)."
  value       = "https://${azurerm_container_app.api.ingress[0].fqdn}"
}

output "resource_group" {
  value = azurerm_resource_group.this.name
}

output "registry" {
  description = "Where scripts/cloud_images.sh pushes the images."
  value       = azurerm_container_registry.this.name
}

output "key_vault" {
  description = "Holds the role keys: az keyvault secret show --vault-name <this> --name submitter-api-keys"
  value       = azurerm_key_vault.this.name
}
