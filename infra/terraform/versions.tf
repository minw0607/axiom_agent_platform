terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.12"
    }
  }

  # State stays on the machine that runs Terraform (gitignored). It holds the generated
  # secrets, so treat the state file like a password. A remote backend can come later.
}

provider "azurerm" {
  features {
    key_vault {
      # A demo vault is torn down with the rest; don't keep it for 90 days.
      purge_soft_delete_on_destroy = true
    }
  }
  subscription_id = var.subscription_id
}
