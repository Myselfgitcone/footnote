# One-time setup, applied by hand with an admin login (CloudShell works):
#   - an S3 bucket for Terraform's state
#   - a deploy user for GitHub Actions, allowed to manage footnote-* resources only
# After this, every change goes through CI (infra/main).
#
# Why a user and not GitHub OIDC: accounts made with AWS's new sign-up sit in an
# organization whose service control policy denies iam:*Provider* on both the
# Free and Paid plans, so the OIDC provider can't be created. The user's access
# key is made by hand in the console, so it never lands in Terraform state.

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.90" }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { project = "footnote", managed_by = "terraform" }
  }
}

variable "region" {
  type    = string
  default = "us-east-2" # new-sign-up projects are pinned to Ohio until advanced features are on
}


data "aws_caller_identity" "me" {}

locals {
  account = data.aws_caller_identity.me.account_id
}

resource "aws_s3_bucket" "state" {
  bucket = "footnote-tfstate-${local.account}"
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_iam_user" "deploy" {
  name = "footnote-deploy"
}

# Enough to manage Footnote's own resources, all named footnote-*, and nothing else.
data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "State"
    actions   = ["s3:ListBucket", "s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
  }
  statement {
    sid       = "DataBucket"
    actions   = ["s3:*"]
    resources = ["arn:aws:s3:::footnote-data-*", "arn:aws:s3:::footnote-data-*/*"]
  }
  statement {
    sid       = "Functions"
    actions   = ["lambda:*"]
    resources = ["arn:aws:lambda:${var.region}:${local.account}:function:footnote-*"]
  }
  statement {
    sid = "Roles"
    actions = [
      "iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:UpdateRole", "iam:TagRole", "iam:UntagRole",
      "iam:PutRolePolicy", "iam:GetRolePolicy", "iam:DeleteRolePolicy", "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies", "iam:AttachRolePolicy", "iam:DetachRolePolicy",
      "iam:ListInstanceProfilesForRole", "iam:PassRole",
    ]
    resources = ["arn:aws:iam::${local.account}:role/footnote-*"]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:*"]
    resources = ["arn:aws:logs:${var.region}:${local.account}:log-group:/aws/lambda/footnote-*"]
  }
  statement {
    sid       = "LogsDescribe"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
  }
  statement {
    sid       = "Schedules"
    actions   = ["scheduler:*"]
    resources = ["arn:aws:scheduler:${var.region}:${local.account}:schedule/default/footnote-*"]
  }
  statement {
    sid       = "Budgets"
    actions   = ["budgets:*"]
    resources = ["arn:aws:budgets::${local.account}:budget/footnote-*"]
  }
}

resource "aws_iam_user_policy" "deploy" {
  name   = "footnote-deploy"
  user   = aws_iam_user.deploy.name
  policy = data.aws_iam_policy_document.deploy.json
}

output "state_bucket" {
  value = aws_s3_bucket.state.bucket
}

output "deploy_user" {
  value = aws_iam_user.deploy.name
}
