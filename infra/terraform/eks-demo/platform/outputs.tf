output "namespace" { value = local.namespace }
output "public_url" { value = var.deploy_application && var.public_ingress_enabled ? "https://${data.terraform_remote_state.foundation.outputs.public_hostname}" : null }
output "alb_arn" { value = var.deploy_application && var.public_ingress_enabled ? data.aws_lb.rhetoriq[0].arn : null }
