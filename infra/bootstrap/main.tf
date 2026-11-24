# Bootstrap (se ejecuta UNA vez, a mano, con tus credenciales de AWS):
#   - bucket de S3 para el estado de Terraform (versionado, cifrado, privado)
#   - proveedor OIDC de GitHub + rol que GitHub Actions asume SIN claves de acceso
#
# Es el huevo y la gallina de GitHub Actions: antes de que el pipeline pueda desplegar nada, tiene que
# existir el rol que asume y el bucket donde guarda el estado. Por eso esto se aplica fuera del pipeline
# (su propio estado es local y no se sube al repo; si lo pierdes, basta con `terraform import`).

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.23"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project
      ManagedBy = "terraform-bootstrap"
    }
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
}

# ---------------------------------------------------------------------------------------------------
# Estado de Terraform
# ---------------------------------------------------------------------------------------------------
resource "aws_s3_bucket" "tfstate" {
  bucket = "${var.project}-tfstate-${local.account_id}"
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "tfstate_tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.tfstate.arn, "${aws_s3_bucket.tfstate.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "tfstate" {
  bucket     = aws_s3_bucket.tfstate.id
  policy     = data.aws_iam_policy_document.tfstate_tls_only.json
  depends_on = [aws_s3_bucket_public_access_block.tfstate]
}

# ---------------------------------------------------------------------------------------------------
# GitHub Actions -> AWS mediante OIDC (sin credenciales de larga duracion en GitHub)
# ---------------------------------------------------------------------------------------------------
# Solo puede haber UN proveedor OIDC de GitHub por cuenta: si ya existe, usa create_github_oidc_provider=false.
resource "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 0 : 1

  url = "https://token.actions.githubusercontent.com"
}

locals {
  oidc_provider_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

data "aws_iam_policy_document" "github_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [local.oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Solo workflows de ESTE repositorio pueden asumir el rol.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:*"]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name               = "${var.project}-github-deploy"
  description        = "Rol que asume GitHub Actions (OIDC) para desplegar con Terraform y ejecutar el pipeline"
  assume_role_policy = data.aws_iam_policy_document.github_trust.json
}

# PowerUserAccess permite crear/gestionar todos los servicios (S3, RDS, Lambda, ECS, ECR, Bedrock...)
# pero NO IAM. IAM se concede aparte y acotado a los roles del proyecto (prefijo "<project>-").
resource "aws_iam_role_policy_attachment" "deploy_power_user" {
  role       = aws_iam_role.deploy.name
  policy_arn = "arn:aws:iam::aws:policy/PowerUserAccess"
}

data "aws_iam_policy_document" "deploy_iam" {
  statement {
    sid = "ManageProjectRoles"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:UpdateRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:PassRole",
      "iam:PutRolePolicy",
      "iam:GetRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
    ]
    resources = ["arn:aws:iam::${local.account_id}:role/${var.project}-*"]
  }

  # Roles vinculados a servicio que AWS crea la primera vez (ECS, ELB, RDS, autoscaling...).
  statement {
    sid       = "ServiceLinkedRoles"
    actions   = ["iam:CreateServiceLinkedRole", "iam:GetRole"]
    resources = ["arn:aws:iam::${local.account_id}:role/aws-service-role/*"]
  }
}

resource "aws_iam_role_policy" "deploy_iam" {
  name   = "${var.project}-deploy-iam"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy_iam.json
}
