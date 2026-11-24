variable "aws_region" {
  description = "Region de AWS (Bedrock con perfil de inferencia UE, Aurora Serverless v2 y ECS Express Mode estan disponibles en Paris)."
  type        = string
  default     = "eu-west-3"
}

variable "project" {
  description = "Prefijo de los recursos."
  type        = string
  default     = "kid"
}

# ---- Base de datos --------------------------------------------------------------------------------
variable "aurora_engine_version" {
  description = "Version exacta de Aurora PostgreSQL. Vacio = la mas reciente disponible en la region de entre las 16.x (la auto-pausa exige 16.3+)."
  type        = string
  default     = ""
}

variable "aurora_min_acu" {
  description = "ACU minimas. 0 = la base se pausa sola y no cuesta capacidad cuando no se usa (0,5 ACU 24/7 serian ~51 USD/mes)."
  type        = number
  default     = 0
}

variable "aurora_max_acu" {
  description = "ACU maximas (1 ACU ~ 2 GiB de RAM). 2 sobra para este proyecto."
  type        = number
  default     = 2
}

variable "aurora_auto_pause_seconds" {
  description = "Segundos sin conexiones antes de pausar (300-86400). Sube este valor el dia de la demo para evitar la espera de reanudacion."
  type        = number
  default     = 600
}

# ---- API (Lambda) ---------------------------------------------------------------------------------
variable "lambda_zip_path" {
  description = "Paquete de la Lambda generado por scripts/build_lambda.sh"
  type        = string
  default     = "../../build/api.zip"
}

variable "api_memory_mb" {
  type    = number
  default = 512
}

variable "api_timeout_s" {
  description = "Margen para la reanudacion de Aurora tras la auto-pausa (~15 s) mas la consulta."
  type        = number
  default     = 60
}

# ---- Web (ECS Express Mode) -----------------------------------------------------------------------
variable "deploy_web_app" {
  description = "Desplegar la app Streamlit en ECS Express Mode (ALB + Fargate: es lo que mas cuesta, ~1,4 USD/dia). false = solo API + datos."
  type        = bool
  default     = true
}

variable "app_image_tag" {
  description = "Etiqueta de la imagen en ECR. Vacia = todavia no hay imagen: se crea el repositorio pero no el servicio."
  type        = string
  default     = ""
}

variable "web_cpu" {
  description = "CPU de la tarea Fargate (256 = 0,25 vCPU)."
  type        = number
  default     = 256
}

variable "web_memory_mb" {
  description = "Memoria de la tarea Fargate en MiB."
  type        = number
  default     = 1024
}

variable "web_max_tasks" {
  description = "Tope de tareas del autoescalado (limita el gasto ante un pico de trafico)."
  type        = number
  default     = 2
}

variable "force_destroy_bucket" {
  description = "Permitir que `terraform destroy` borre el bucket aunque contenga PDFs (se pueden regenerar con el scraper)."
  type        = bool
  default     = true
}

# ---- Control de costes ----------------------------------------------------------------------------
variable "budget_alert_email" {
  description = "Correo al que avisar si el gasto mensual se acerca al presupuesto. Vacio = sin alerta."
  type        = string
  default     = ""
}

variable "monthly_budget_usd" {
  description = "Presupuesto mensual (USD) de la alerta."
  type        = number
  default     = 40
}
