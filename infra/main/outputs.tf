output "data_bucket" {
  value = aws_s3_bucket.data.bucket
}

output "ingest_function" {
  value = aws_lambda_function.ingest.function_name
}

output "index_function" {
  value = aws_lambda_function.index.function_name
}

output "ask_url" {
  value = aws_lambda_function_url.ask.function_url
}
