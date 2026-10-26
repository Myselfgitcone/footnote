variable "region" {
  type    = string
  default = "us-east-2"
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

variable "pinecone_api_key" {
  type        = string
  description = "From the PINECONE_API_KEY repository secret. Ends up in the Lambda's environment and in Terraform state, both encrypted."
  sensitive   = true
}

variable "anthropic_api_key" {
  type        = string
  description = "From the ANTHROPIC_API_KEY repository secret: Claude, which plans and writes answers."
  sensitive   = true
}

variable "langsmith_api_key" {
  type        = string
  description = "From the LANGSMITH_API_KEY repository secret: traces of every answer. Empty turns tracing off."
  sensitive   = true
  default     = ""
}

variable "allowed_origins" {
  type        = list(string)
  description = "Web pages allowed to call the API from a browser."
  default     = ["https://myselfgitcone.github.io", "http://localhost:8000"]
}
