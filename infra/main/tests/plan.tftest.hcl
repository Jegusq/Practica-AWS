# Tests con un provider de AWS SIMULADO (mock_provider: `apply` no crea nada real) (no tocan AWS ni necesitan credenciales).
# Comprueban la logica condicional del stack: que se crea cada recurso segun las variables.
#
#   cd infra/main && terraform init -backend=false && bash ../../scripts/build_lambda.sh && terraform test

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }

  mock_data "aws_vpc" {
    defaults = {
      id = "vpc-0123456789abcdef0"
    }
  }

  mock_data "aws_subnets" {
    defaults = {
      ids = ["subnet-a", "subnet-b", "subnet-c"]
    }
  }

  mock_data "aws_rds_engine_version" {
    defaults = {
      version = "16.6"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  mock_resource "aws_rds_cluster" {
    defaults = {
      arn            = "arn:aws:rds:eu-west-3:123456789012:cluster:kid-aurora"
      database_name  = "kid"
      engine         = "aurora-postgresql"
      engine_version = "16.6"
      master_user_secret = [{
        secret_arn    = "arn:aws:secretsmanager:eu-west-3:123456789012:secret:rds!cluster-abc"
        kms_key_id    = "alias/aws/secretsmanager"
        secret_status = "active"
      }]
    }
  }

  mock_resource "aws_lambda_function_url" {
    defaults = {
      function_url = "https://abc123.lambda-url.eu-west-3.on.aws/"
    }
  }

  mock_resource "aws_ecr_repository" {
    defaults = {
      repository_url = "123456789012.dkr.ecr.eu-west-3.amazonaws.com/kid-app"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/kid-mock"
    }
  }

  mock_resource "aws_ecs_express_gateway_service" {
    defaults = {
      ingress_paths = [{
        access_type = "PUBLIC"
        endpoint    = "kid-web.ecs.eu-west-3.on.aws"
      }]
    }
  }
}

variables {
  lambda_zip_path = "../../build/api.zip"
}

run "web_desactivada_no_crea_nada_de_ecs" {
  command = apply

  variables {
    deploy_web_app = false
  }

  assert {
    condition     = length(aws_ecr_repository.app) == 0 && length(aws_ecs_cluster.app) == 0 && length(aws_ecs_express_gateway_service.web) == 0
    error_message = "con deploy_web_app=false no debe existir ningun recurso de la web"
  }
}

run "web_sin_imagen_crea_ecr_pero_no_el_servicio" {
  command = apply

  variables {
    deploy_web_app = true
    app_image_tag  = ""
  }

  assert {
    condition     = length(aws_ecr_repository.app) == 1 && length(aws_ecs_express_gateway_service.web) == 0
    error_message = "sin imagen debe existir el repositorio ECR pero no el servicio (la imagen se sube entre medias)"
  }
}

run "web_con_imagen_crea_el_servicio_bien_configurado" {
  command = apply

  variables {
    deploy_web_app = true
    app_image_tag  = "abc123def456"
  }

  assert {
    condition     = length(aws_ecs_express_gateway_service.web) == 1
    error_message = "con imagen debe crearse el servicio ECS Express"
  }

  assert {
    condition     = aws_ecs_express_gateway_service.web[0].primary_container[0].container_port == 8501
    error_message = "Streamlit escucha en el 8501"
  }

  assert {
    condition     = aws_ecs_express_gateway_service.web[0].health_check_path == "/_stcore/health"
    error_message = "el health check debe ser el de Streamlit"
  }

  assert {
    condition     = endswith(aws_ecs_express_gateway_service.web[0].primary_container[0].image, ":abc123def456")
    error_message = "la imagen debe llevar la etiqueta indicada"
  }

  assert {
    condition     = aws_ecs_express_gateway_service.web[0].cpu == "256" && aws_ecs_express_gateway_service.web[0].memory == "1024"
    error_message = "CPU/memoria por defecto: 256 / 1024"
  }

  assert {
    condition     = aws_ecs_express_gateway_service.web[0].scaling_target[0].max_task_count == 2
    error_message = "el autoescalado debe estar acotado (control de gasto)"
  }
}

run "aurora_se_pausa_cuando_min_acu_es_cero" {
  command = apply

  assert {
    condition     = aws_rds_cluster.kid.serverlessv2_scaling_configuration[0].min_capacity == 0 && aws_rds_cluster.kid.serverlessv2_scaling_configuration[0].seconds_until_auto_pause == 600
    error_message = "por defecto: min 0 ACU con auto-pausa a los 600 s"
  }

  assert {
    condition     = aws_rds_cluster.kid.enable_http_endpoint == true && aws_rds_cluster.kid.manage_master_user_password == true && aws_rds_cluster.kid.storage_encrypted == true
    error_message = "Data API activa, contrasena gestionada por RDS y almacenamiento cifrado"
  }

  assert {
    condition     = aws_rds_cluster_instance.writer.instance_class == "db.serverless" && aws_rds_cluster_instance.writer.publicly_accessible == false
    error_message = "instancia Serverless v2 y no publica"
  }
}

run "min_acu_configurable" {
  command = apply

  variables {
    aurora_min_acu = 0.5
  }

  # (seconds_until_auto_pause es opcional+calculado en el provider: con el provider simulado conserva el
  # valor del run anterior, asi que aqui solo se comprueba la capacidad minima que llega al cluster.)
  assert {
    condition     = aws_rds_cluster.kid.serverlessv2_scaling_configuration[0].min_capacity == 0.5
    error_message = "la capacidad minima debe poder subirse (p. ej. el dia de la demo, para no esperar la reanudacion)"
  }
}

run "bucket_privado_y_cifrado" {
  command = apply

  assert {
    condition     = aws_s3_bucket.kids.bucket == "kid-kids-123456789012"
    error_message = "el nombre del bucket lleva el id de la cuenta"
  }

  assert {
    condition = (
      aws_s3_bucket_public_access_block.kids.block_public_acls == true &&
      aws_s3_bucket_public_access_block.kids.block_public_policy == true &&
      aws_s3_bucket_public_access_block.kids.ignore_public_acls == true &&
      aws_s3_bucket_public_access_block.kids.restrict_public_buckets == true
    )
    error_message = "los datos no deben ser publicos"
  }
}

run "api_publica_con_los_dos_permisos_de_function_url" {
  command = apply

  assert {
    condition     = aws_lambda_function_url.api.authorization_type == "NONE"
    error_message = "la API es publica de solo lectura"
  }

  assert {
    condition     = aws_lambda_permission.url_public.action == "lambda:InvokeFunctionUrl" && aws_lambda_permission.invoke_via_url.action == "lambda:InvokeFunction" && aws_lambda_permission.invoke_via_url.invoked_via_function_url == true
    error_message = "una Function URL publica necesita InvokeFunctionUrl e InvokeFunction (via URL)"
  }

  assert {
    condition     = aws_lambda_function.api.runtime == "python3.12" && aws_lambda_function.api.handler == "kid.api.lambda_handler.handler"
    error_message = "runtime y handler de la Lambda"
  }
}

run "presupuesto_solo_si_hay_correo" {
  command = apply

  assert {
    condition     = length(aws_budgets_budget.monthly) == 0
    error_message = "sin correo no se crea la alerta"
  }
}

run "presupuesto_con_correo" {
  command = apply

  variables {
    budget_alert_email = "alertas@ejemplo.com"
  }

  assert {
    condition     = length(aws_budgets_budget.monthly) == 1
    error_message = "con correo se crea el presupuesto"
  }
}
