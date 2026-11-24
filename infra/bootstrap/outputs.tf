output "account_id" {
  value = local.account_id
}

output "aws_region" {
  value = var.aws_region
}

output "tf_state_bucket" {
  description = "Poner en la variable de repositorio de GitHub TF_STATE_BUCKET"
  value       = aws_s3_bucket.tfstate.bucket
}

output "deploy_role_arn" {
  description = "Poner en la variable de repositorio de GitHub AWS_ROLE_ARN"
  value       = aws_iam_role.deploy.arn
}
