variable "region" {
  type    = string
  default = "us-east-2"
}
variable "state_bucket_name" { type = string }
variable "tags" { type = map(string) }
