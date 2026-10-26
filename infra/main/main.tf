# Footnote's AWS side: a bucket for filings, the ingest and index Lambdas,
# their daily schedules, and a budget alarm. Applied by GitHub Actions on every merge.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.90" }
  }
  backend "s3" {
    key          = "footnote/main.tfstate"
    region       = "us-east-2"
    encrypt      = true
    use_lockfile = true # S3-native state locking; no DynamoDB table needed
    # bucket comes from -backend-config, since it includes the account id
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "footnote", managed_by = "terraform" }
  }
}

data "aws_caller_identity" "me" {}

locals {
  account = data.aws_caller_identity.me.account_id
  bucket  = "footnote-data-${local.account}"
}

# ---------------------------------------------------------------- storage

resource "aws_s3_bucket" "data" {
  bucket = local.bucket
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Versions protect against a bad run overwriting good data; a month is plenty.
resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    id     = "expire-old-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
}

# ---------------------------------------------------------------- code

# Both Lambdas run the same package. At about 50MB zipped it goes through S3;
# Lambda only takes uploads that size directly up to 50MB.
resource "aws_s3_object" "code" {
  bucket      = aws_s3_bucket.data.id
  key         = "artifacts/lambda.zip"
  source      = var.lambda_zip
  source_hash = filemd5(var.lambda_zip)
}

# ---------------------------------------------------------------- ingest Lambda

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingest" {
  name               = "footnote-ingest"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "ingest" {
  statement {
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/*"]
  }
  statement {
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.ingest.arn}:*"]
  }
}

resource "aws_iam_role_policy" "ingest" {
  name   = "footnote-ingest"
  role   = aws_iam_role.ingest.id
  policy = data.aws_iam_policy_document.ingest.json
}

resource "aws_cloudwatch_log_group" "ingest" {
  name              = "/aws/lambda/footnote-ingest"
  retention_in_days = 14
}

resource "aws_lambda_function" "ingest" {
  function_name     = "footnote-ingest"
  role              = aws_iam_role.ingest.arn
  runtime           = "python3.12"
  architectures     = ["x86_64"]
  handler           = "footnote.handler.ingest_handler"
  s3_bucket         = aws_s3_object.code.bucket
  s3_key            = aws_s3_object.code.key
  s3_object_version = aws_s3_object.code.version_id
  source_code_hash  = filebase64sha256(var.lambda_zip)
  timeout           = 900  # 15 minutes, the Lambda maximum; each run is capped to fit
  memory_size       = 1024 # parsing a 10-K's HTML wants the room
  environment {
    variables = {
      FOOTNOTE_BUCKET     = aws_s3_bucket.data.bucket
      FOOTNOTE_USER_AGENT = var.sec_user_agent
    }
  }
  depends_on = [aws_cloudwatch_log_group.ingest, aws_iam_role_policy.ingest]
}

# ---------------------------------------------------------------- index Lambda

resource "aws_iam_role" "index" {
  name               = "footnote-index"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}

data "aws_iam_policy_document" "index" {
  statement {
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.data.arn}/clean/*",
      "${aws_s3_bucket.data.arn}/index/*",
      "${aws_s3_bucket.data.arn}/models/*",
    ]
  }
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/index/*"]
  }
  statement {
    # Without it, asking for a marker that isn't there yet is "access denied", not "not found".
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
  }
  statement {
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.index.arn}:*"]
  }
}

resource "aws_iam_role_policy" "index" {
  name   = "footnote-index"
  role   = aws_iam_role.index.id
  policy = data.aws_iam_policy_document.index.json
}

resource "aws_cloudwatch_log_group" "index" {
  name              = "/aws/lambda/footnote-index"
  retention_in_days = 14
}

resource "aws_lambda_function" "index" {
  function_name     = "footnote-index"
  role              = aws_iam_role.index.arn
  runtime           = "python3.12"
  architectures     = ["x86_64"]
  handler           = "footnote.handler.index_handler"
  s3_bucket         = aws_s3_object.code.bucket
  s3_key            = aws_s3_object.code.key
  s3_object_version = aws_s3_object.code.version_id
  source_code_hash  = filebase64sha256(var.lambda_zip)
  timeout           = 900
  memory_size       = 3008 # Lambda's CPU share grows with memory; embedding is CPU-bound
  ephemeral_storage {
    size = 1024 # the model is copied to /tmp
  }
  environment {
    variables = {
      FOOTNOTE_BUCKET  = aws_s3_bucket.data.bucket
      PINECONE_API_KEY = var.pinecone_api_key
    }
  }
  depends_on = [aws_cloudwatch_log_group.index, aws_iam_role_policy.index]
}

# ---------------------------------------------------------------- daily schedule

data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "footnote-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_trust.json
}

resource "aws_iam_role_policy" "scheduler" {
  name = "footnote-scheduler"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = [aws_lambda_function.ingest.arn, aws_lambda_function.index.arn]
    }]
  })
}

# Every morning, after the SEC's overnight batch is up.
resource "aws_scheduler_schedule" "daily" {
  name                         = "footnote-daily-ingest"
  schedule_expression          = "cron(30 7 * * ? *)"
  schedule_expression_timezone = "America/Chicago"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_lambda_function.ingest.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ max_new = 40 })
    retry_policy { maximum_retry_attempts = 1 }
  }
}

# Once the morning's filings are in, embed them. A run with nothing new takes seconds.
resource "aws_scheduler_schedule" "daily_index" {
  name                         = "footnote-daily-index"
  schedule_expression          = "cron(15 8 * * ? *)"
  schedule_expression_timezone = "America/Chicago"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = aws_lambda_function.index.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ budget_s = 600 })
    retry_policy { maximum_retry_attempts = 1 }
  }
}

# ---------------------------------------------------------------- cost guard

resource "aws_budgets_budget" "monthly" {
  name         = "footnote-monthly"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }
  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}
