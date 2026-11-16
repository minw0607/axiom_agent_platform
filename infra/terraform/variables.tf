variable "subscription_id" {
  description = "The Azure subscription to deploy into: a personal one, never an employer's."
  type        = string
}

variable "location" {
  description = "Azure region. Prices in ADR 0009 are for eastus."
  type        = string
  default     = "eastus"
}

variable "prefix" {
  description = "Short lowercase name used in every resource name."
  type        = string
  default     = "axiom"

  validation {
    condition     = can(regex("^[a-z][a-z0-9]{2,9}$", var.prefix))
    error_message = "Use 3 to 10 lowercase letters or digits, starting with a letter."
  }
}

variable "platform_image_tag" {
  description = "Tag of the axiom-platform image in the registry (api, worker, tool gateway)."
  type        = string
  default     = "latest"
}

variable "gkn_image_tag" {
  description = "Tag of the gkn-cloud image: GKN plus the reference collections."
  type        = string
  default     = "latest"
}

variable "regulus_image_tag" {
  description = "Tag of the regulus-mcp image."
  type        = string
  default     = "0.2.1"
}

variable "daily_job_cap" {
  description = "New memo jobs accepted per UTC day across all users."
  type        = number
  default     = 25

  validation {
    condition     = var.daily_job_cap >= 1
    error_message = "The cap must be at least 1."
  }
}

variable "monthly_budget" {
  description = "Monthly budget for the resource group, in the billing currency."
  type        = number
  default     = 30
}

variable "budget_emails" {
  description = "Who gets the budget alerts. Set it in terraform.tfvars, which is gitignored."
  type        = list(string)

  validation {
    condition     = length(var.budget_emails) > 0
    error_message = "Give at least one address for budget alerts."
  }
}

variable "log_daily_quota_gb" {
  description = "Log Analytics stops ingesting for the day after this much. 5 GB a month is free."
  type        = number
  default     = 0.5
}
