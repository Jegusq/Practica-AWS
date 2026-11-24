variable "aws_region" {
  description = "Region de AWS donde se crea todo (Paris: Bedrock con perfil de inferencia UE, Aurora Serverless v2 y ECS Express Mode)."
  type        = string
  default     = "eu-west-3"
}

variable "project" {
  description = "Prefijo de los recursos. Los roles de IAM del proyecto DEBEN empezar por '<project>-'."
  type        = string
  default     = "kid"
}

variable "github_repository" {
  description = "Repositorio de GitHub (propietario/nombre) autorizado a asumir el rol de despliegue."
  type        = string
  default     = "Jegusq/Practica-AWS"
}

variable "create_github_oidc_provider" {
  description = "Crear el proveedor OIDC de GitHub. Ponlo a false si la cuenta ya lo tiene (solo puede haber uno)."
  type        = bool
  default     = true
}
