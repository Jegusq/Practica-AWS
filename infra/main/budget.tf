# Alerta de gasto (opcional): avisa por correo cuando el gasto de la cuenta se acerca o se prevé que supere
# el presupuesto mensual. AWS Budgets es gratuito para los dos primeros presupuestos de la cuenta.
# Se activa informando `budget_alert_email` (variable de repositorio BUDGET_ALERT_EMAIL en GitHub).

resource "aws_budgets_budget" "monthly" {
  count = var.budget_alert_email == "" ? 0 : 1

  name         = "${local.name}-mensual"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.budget_alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_alert_email]
  }
}
