output "data_bucket" {
  value = aws_s3_bucket.data.bucket
}

output "ingest_function" {
  value = aws_lambda_function.ingest.function_name
}
