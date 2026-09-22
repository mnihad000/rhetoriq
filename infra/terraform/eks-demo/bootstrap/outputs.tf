output "state_bucket" { value = aws_s3_bucket.state.id }
output "backend_examples" {
  value = {
    foundation = "bucket=${aws_s3_bucket.state.id}, key=foundation/terraform.tfstate, region=${var.region}, encrypt=true, use_lockfile=true"
    platform   = "bucket=${aws_s3_bucket.state.id}, key=platform/terraform.tfstate, region=${var.region}, encrypt=true, use_lockfile=true"
  }
}
