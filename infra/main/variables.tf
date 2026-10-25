variable "region" {
  type    = string
  default = "us-east-1"
}

variable "lambda_zip" {
  type        = string
  description = "Path to the packaged Lambda code, built by CI."
  default     = "../../build/lambda.zip"
}

variable "sec_user_agent" {
  type        = string
  description = "The SEC asks every client to identify itself with a name and contact email."
  default     = "Footnote research project jagadishbutukuri11@gmail.com"
}

variable "alert_email" {
  type        = string
  description = "Where budget alerts go."
  default     = "jagadishbutukuri11@gmail.com"
}

variable "monthly_budget_usd" {
  type    = string
  default = "1"
}
