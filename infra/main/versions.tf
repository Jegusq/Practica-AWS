terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.23" # aws_ecs_express_gateway_service
    }
  }

  # Estado remoto en S3 con bloqueo nativo (use_lockfile, sin DynamoDB). El bucket y la clave se pasan en
  # `terraform init -backend-config=...` (ver README y .github/workflows/deploy.yml).
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform"
      Repo      = "Practica-AWS"
    }
  }
}

data "aws_caller_identity" "current" {}
