# Web: Streamlit en contenedor, desplegado con Amazon ECS Express Mode.
# Express Mode crea por nosotros el servicio Fargate, el ALB con HTTPS, el autoescalado y la URL.
# Es lo que mas cuesta del proyecto (ALB + IPv4 + Fargate ~ 1,4 USD/dia): `deploy_web_app = false` lo omite.
#
# Orden de despliegue (lo hace .github/workflows/deploy.yml):
#   1) terraform apply -target=aws_ecr_repository.app   -> existe el repositorio
#   2) docker build + push  (etiqueta = SHA del commit)
#   3) terraform apply -var app_image_tag=<sha>          -> se crea/actualiza el servicio

locals {
  web_enabled = var.deploy_web_app
  web_service = var.deploy_web_app && var.app_image_tag != ""
}

resource "aws_ecr_repository" "app" {
  count = local.web_enabled ? 1 : 0

  name                 = "${local.name}-app"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # `terraform destroy` debe poder borrarlo con imagenes dentro

  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  count = local.web_enabled ? 1 : 0

  repository = aws_ecr_repository.app[0].name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Conservar solo las 5 ultimas imagenes"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 5 }
      action       = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "app" {
  count = local.web_enabled ? 1 : 0

  name              = "/ecs/${local.name}-web"
  retention_in_days = 14
}

resource "aws_ecs_cluster" "app" {
  count = local.web_enabled ? 1 : 0

  name = "${local.name}-web"
}

# ---- Roles que exige Express Mode ----------------------------------------------------------------
data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "ecs_infra_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs.amazonaws.com"]
    }
  }
}

# Rol de ejecucion: descargar la imagen de ECR y escribir logs.
resource "aws_iam_role" "web_execution" {
  count = local.web_enabled ? 1 : 0

  name               = "${local.name}-web-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "web_execution" {
  count = local.web_enabled ? 1 : 0

  role       = aws_iam_role.web_execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Rol de infraestructura: lo usa ECS para crear el ALB, los grupos de seguridad y el autoescalado.
resource "aws_iam_role" "web_infrastructure" {
  count = local.web_enabled ? 1 : 0

  name               = "${local.name}-web-infrastructure"
  assume_role_policy = data.aws_iam_policy_document.ecs_infra_trust.json
}

resource "aws_iam_role_policy_attachment" "web_infrastructure" {
  count = local.web_enabled ? 1 : 0

  role       = aws_iam_role.web_infrastructure[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices"
}

# ---- Servicio ------------------------------------------------------------------------------------
resource "aws_ecs_express_gateway_service" "web" {
  count = local.web_service ? 1 : 0

  service_name            = "${local.name}-web"
  cluster                 = aws_ecs_cluster.app[0].name
  execution_role_arn      = aws_iam_role.web_execution[0].arn
  infrastructure_role_arn = aws_iam_role.web_infrastructure[0].arn
  cpu                     = tostring(var.web_cpu)
  memory                  = tostring(var.web_memory_mb)
  health_check_path       = "/_stcore/health"

  primary_container {
    image          = "${aws_ecr_repository.app[0].repository_url}:${var.app_image_tag}"
    container_port = 8501

    aws_logs_configuration {
      log_group         = aws_cloudwatch_log_group.app[0].name
      log_stream_prefix = "web"
    }

    environment {
      name  = "API_URL"
      value = aws_lambda_function_url.api.function_url
    }
  }

  scaling_target {
    min_task_count            = 1
    max_task_count            = var.web_max_tasks
    auto_scaling_metric       = "AVERAGE_CPU"
    auto_scaling_target_value = 70
  }

  wait_for_steady_state = true

  # El rol debe seguir existiendo mientras el servicio se destruye (si no, se queda en DRAINING).
  depends_on = [
    aws_iam_role_policy_attachment.web_execution,
    aws_iam_role_policy_attachment.web_infrastructure,
  ]
}
