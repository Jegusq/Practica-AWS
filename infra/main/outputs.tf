locals {
  web_endpoint = try(aws_ecs_express_gateway_service.web[0].ingress_paths[0].endpoint, null)
}

output "aws_region" {
  value = var.aws_region
}

output "api_url" {
  description = "URL de la API (Lambda Function URL). Documentacion interactiva en <url>docs"
  value       = aws_lambda_function_url.api.function_url
}

output "web_url" {
  description = "URL de la aplicacion Streamlit (vacia hasta que se despliega el servicio)"
  value       = local.web_endpoint == null ? null : (can(regex("^https?://", local.web_endpoint)) ? local.web_endpoint : "https://${local.web_endpoint}")
}

output "bucket" {
  value = aws_s3_bucket.kids.bucket
}

output "db_cluster_arn" {
  value = aws_rds_cluster.kid.arn
}

output "db_secret_arn" {
  value = aws_rds_cluster.kid.master_user_secret[0].secret_arn
}

output "db_name" {
  value = aws_rds_cluster.kid.database_name
}

output "ecr_repository_url" {
  value = try(aws_ecr_repository.app[0].repository_url, null)
}
