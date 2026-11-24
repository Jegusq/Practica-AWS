# API: FastAPI en AWS Lambda (Python 3.12, arm64) expuesta con una Function URL (HTTPS, sin API Gateway).
# El paquete lo genera scripts/build_lambda.sh (build/api.zip) antes de `terraform plan/apply`.

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/lambda/${local.name}-api"
  retention_in_days = 14
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api" {
  name               = "${local.name}-api-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

# Minimo privilegio: solo escribe sus logs, consulta ESTE cluster por la Data API, lee ESTE secreto y
# firma enlaces de lectura a los PDFs del bucket. No puede escribir en la base de datos de otra cuenta
# ni tocar nada mas.
data "aws_iam_policy_document" "api" {
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.api.arn}:*"]
  }

  statement {
    sid = "DataApi"
    actions = [
      "rds-data:ExecuteStatement",
      "rds-data:BatchExecuteStatement",
      "rds-data:BeginTransaction",
      "rds-data:CommitTransaction",
      "rds-data:RollbackTransaction",
    ]
    resources = [aws_rds_cluster.kid.arn]
  }

  statement {
    sid       = "DataApiSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_rds_cluster.kid.master_user_secret[0].secret_arn]
  }

  statement {
    sid       = "ReadPdfs"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.kids.arn}/kids/*"]
  }
}

resource "aws_iam_role_policy" "api" {
  name   = "${local.name}-api"
  role   = aws_iam_role.api.id
  policy = data.aws_iam_policy_document.api.json
}

resource "aws_lambda_function" "api" {
  function_name = "${local.name}-api"
  role          = aws_iam_role.api.arn
  handler       = "kid.api.lambda_handler.handler"
  runtime       = "python3.12"
  architectures = ["arm64"]
  memory_size   = var.api_memory_mb
  timeout       = var.api_timeout_s

  filename         = var.lambda_zip_path
  source_code_hash = filebase64sha256(var.lambda_zip_path)

  environment {
    variables = {
      DB_CLUSTER_ARN = aws_rds_cluster.kid.arn
      DB_SECRET_ARN  = aws_rds_cluster.kid.master_user_secret[0].secret_arn
      DB_NAME        = aws_rds_cluster.kid.database_name
      KID_BUCKET     = aws_s3_bucket.kids.bucket
    }
  }

  depends_on = [aws_cloudwatch_log_group.api, aws_iam_role_policy.api]
}

resource "aws_lambda_function_url" "api" {
  function_name      = aws_lambda_function.api.function_name
  authorization_type = "NONE" # API publica de solo lectura (el enunciado pide una URL de la API)
}

# Desde oct-2025 una Function URL publica necesita DOS permisos: invocar la URL y ser invocada via URL.
resource "aws_lambda_permission" "url_public" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.api.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "invoke_via_url" {
  statement_id             = "AllowInvokeViaFunctionUrl"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.api.function_name
  principal                = "*"
  invoked_via_function_url = true
}
