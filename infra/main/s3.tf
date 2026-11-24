# Bucket privado con los PDFs, los manifiestos y los resultados de la extraccion.
# Los datos NO se exponen publicamente: solo la API (enlaces firmados de 5 min) y la app dan acceso.

locals {
  name   = var.project
  bucket = "${var.project}-kids-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket" "kids" {
  bucket        = local.bucket
  force_destroy = var.force_destroy_bucket
}

resource "aws_s3_bucket_public_access_block" "kids" {
  bucket                  = aws_s3_bucket.kids.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "kids" {
  bucket = aws_s3_bucket.kids.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_versioning" "kids" {
  bucket = aws_s3_bucket.kids.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "kids" {
  bucket = aws_s3_bucket.kids.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "kids" {
  bucket = aws_s3_bucket.kids.id

  rule {
    id     = "limpieza"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.kids]
}

data "aws_iam_policy_document" "kids_tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.kids.arn, "${aws_s3_bucket.kids.arn}/*"]

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

resource "aws_s3_bucket_policy" "kids" {
  bucket     = aws_s3_bucket.kids.id
  policy     = data.aws_iam_policy_document.kids_tls_only.json
  depends_on = [aws_s3_bucket_public_access_block.kids]
}
