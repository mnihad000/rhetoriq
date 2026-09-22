variable "region" {
  type    = string
  default = "us-east-2"
}
variable "state_bucket" { type = string }
variable "foundation_state_key" {
  type    = string
  default = "foundation/terraform.tfstate"
}
variable "backend_digest" {
  type    = string
  default = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
}
variable "b5_digest" {
  type    = string
  default = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
}
variable "flink_digest" {
  type    = string
  default = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
}
variable "frontend_digest" {
  type    = string
  default = "sha256:0000000000000000000000000000000000000000000000000000000000000000"
}
variable "deploy_application" {
  type    = bool
  default = false
}
variable "smoke_enabled" {
  type    = bool
  default = false
}
variable "evidence_enabled" {
  type    = bool
  default = false
}
variable "public_ingress_enabled" {
  type    = bool
  default = false
}
variable "allowed_cidrs" {
  type    = list(string)
  default = []
}
variable "cert_manager_version" {
  type    = string
  default = "v1.21.2"
}
variable "trust_manager_version" {
  type    = string
  default = "v0.24.0"
}
variable "load_balancer_controller_chart_version" {
  type    = string
  default = "3.5.0"
}

check "application_inputs" {
  assert {
    condition = !var.deploy_application || alltrue([
      for digest in [var.backend_digest, var.b5_digest, var.flink_digest, var.frontend_digest] :
      can(regex("^sha256:[0-9a-f]{64}$", digest)) && digest != "sha256:${strrepeat("0", 64)}"
    ])
    error_message = "Application deployment requires four non-placeholder sha256 image digests."
  }
  assert {
    condition     = !var.public_ingress_enabled || (var.deploy_application && length(var.allowed_cidrs) > 0)
    error_message = "Public ingress requires application deployment and at least one allowed CIDR."
  }
}

locals {
  namespace = "rhetoriq-demo"
  alb_name  = substr("rhetoriq-${data.terraform_remote_state.foundation.outputs.cluster_name}", 0, 32)
}
