# A monthly budget on the resource group, with alerts before it is spent. A budget alerts;
# it does not stop anything. Tearing down is `terraform destroy`.

resource "azurerm_consumption_budget_resource_group" "this" {
  name              = "budget-${local.name}"
  resource_group_id = azurerm_resource_group.this.id
  amount            = var.monthly_budget
  time_grain        = "Monthly"

  time_period {
    start_date = formatdate("YYYY-MM-01'T'00:00:00Z", timestamp())
  }

  notification {
    enabled        = true
    threshold      = 50
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = var.budget_emails
  }

  notification {
    enabled        = true
    threshold      = 80
    operator       = "GreaterThan"
    threshold_type = "Actual"
    contact_emails = var.budget_emails
  }

  notification {
    enabled        = true
    threshold      = 100
    operator       = "GreaterThan"
    threshold_type = "Forecasted"
    contact_emails = var.budget_emails
  }

  lifecycle {
    # The start month is fixed when the budget is created.
    ignore_changes = [time_period]
  }
}
