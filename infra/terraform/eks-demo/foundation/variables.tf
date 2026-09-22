variable "region" {
  type    = string
  default = "us-east-2"
}
variable "run_id" { type = string }
variable "owner" { type = string }
variable "commit" { type = string }
variable "expires_at" {
  type = string
  validation {
    condition     = can(timecmp(var.expires_at, timestamp()))
    error_message = "expires_at must be an RFC3339 timestamp."
  }
}
variable "operator_principal_arn" { type = string }
variable "operator_cidrs" { type = list(string) }
variable "ci_cidrs" {
  type    = list(string)
  default = []
}
variable "notification_email" { type = string }
variable "kubernetes_version" {
  type    = string
  default = "1.36"
}
variable "node_instance_type" {
  type    = string
  default = "m7i.2xlarge"
}
variable "network_policy_enforcing_mode" {
  type    = string
  default = "strict"
  validation {
    condition     = contains(["standard", "strict"], var.network_policy_enforcing_mode)
    error_message = "network_policy_enforcing_mode must be standard or strict."
  }
}
variable "public_ingress_enabled" {
  type    = bool
  default = false
}
variable "hosted_zone_id" {
  type    = string
  default = ""
}
variable "subdomain" {
  type    = string
  default = ""
}

locals {
  name = "rhetoriq-${var.run_id}"
  tags = {
    project     = "rhetoriq"
    environment = "portfolio-demo"
    owner       = var.owner
    run-id      = var.run_id
    commit      = var.commit
    expires-at  = var.expires_at
  }
  public_access_cidrs = distinct(concat(var.operator_cidrs, var.ci_cidrs))
}
