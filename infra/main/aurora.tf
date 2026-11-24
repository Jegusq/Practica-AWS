# Aurora PostgreSQL Serverless v2 con RDS Data API.
#
# Decisiones (ver README):
#  - Serverless v2 con min 0 ACU: se pausa tras `aurora_auto_pause_seconds` sin uso y no se paga capacidad
#    mientras esta en pausa (solo el almacenamiento, centimos). Se reanuda sola con la primera peticion
#    de la Data API (~15 s); el cliente reintenta mientras tanto.
#  - Data API (HTTPS + IAM): la Lambda no necesita VPC, ni NAT, ni drivers, ni pool de conexiones.
#  - Credenciales gestionadas por RDS en Secrets Manager (ni en el repo ni en el estado de Terraform).
#  - VPC por defecto: sin NAT ni recursos de red adicionales. El cluster NO es accesible desde Internet
#    (sin ingreso en su grupo de seguridad y sin acceso publico).

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# Version de Aurora PostgreSQL: la mas reciente disponible EN LA REGION de entre estas (todas >= 16.3, que es
# lo que exige la auto-pausa). Si se informa var.aurora_engine_version se usa esa exacta.
data "aws_rds_engine_version" "aurora" {
  engine = "aurora-postgresql"
  preferred_versions = var.aurora_engine_version != "" ? [var.aurora_engine_version] : [
    "16.11", "16.10", "16.9", "16.8", "16.6", "16.4", "16.3",
  ]
}

resource "aws_db_subnet_group" "kid" {
  name       = "${local.name}-aurora"
  subnet_ids = data.aws_subnets.default.ids
}

resource "aws_security_group" "aurora" {
  name        = "${local.name}-aurora"
  description = "Aurora de ${local.name}: sin trafico entrante (se accede por la Data API)"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_rds_cluster" "kid" {
  cluster_identifier = "${local.name}-aurora"
  engine             = "aurora-postgresql"
  engine_mode        = "provisioned" # Serverless v2 usa "provisioned" + instancias db.serverless
  engine_version     = data.aws_rds_engine_version.aurora.version
  database_name      = "kid"
  master_username    = "kidadmin"

  manage_master_user_password = true
  enable_http_endpoint        = true # RDS Data API

  db_subnet_group_name   = aws_db_subnet_group.kid.name
  vpc_security_group_ids = [aws_security_group.aurora.id]
  storage_encrypted      = true

  backup_retention_period = 1
  copy_tags_to_snapshot   = true
  # Proyecto academico: `terraform destroy` debe poder borrarlo sin dejar snapshots que cuesten.
  skip_final_snapshot = true
  deletion_protection = false
  apply_immediately   = true

  serverlessv2_scaling_configuration {
    min_capacity             = var.aurora_min_acu
    max_capacity             = var.aurora_max_acu
    seconds_until_auto_pause = var.aurora_min_acu == 0 ? var.aurora_auto_pause_seconds : null
  }

  # AWS puede subir la version menor sola; no queremos que un `apply` posterior intente "revertirla".
  lifecycle {
    ignore_changes = [engine_version]
  }
}

resource "aws_rds_cluster_instance" "writer" {
  identifier         = "${local.name}-aurora-1"
  cluster_identifier = aws_rds_cluster.kid.id
  engine             = aws_rds_cluster.kid.engine
  engine_version     = aws_rds_cluster.kid.engine_version
  instance_class     = "db.serverless"

  publicly_accessible = false

  lifecycle {
    ignore_changes = [engine_version]
  }
}
