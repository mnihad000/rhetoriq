output "cluster_name" {
  value = aws_eks_cluster.demo.name
}
output "cluster_endpoint" {
  value     = aws_eks_cluster.demo.endpoint
  sensitive = true
}
output "cluster_ca" {
  value     = aws_eks_cluster.demo.certificate_authority[0].data
  sensitive = true
}
output "vpc_id" {
  value = aws_vpc.demo.id
}
output "vpc_cidr" {
  value = aws_vpc.demo.cidr_block
}
output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}
output "ecr_repositories" {
  value = { for name, repository in aws_ecr_repository.app : name => repository.repository_url }
}
output "load_balancer_controller_role_arn" {
  value = aws_iam_role.load_balancer_controller.arn
}
output "public_certificate_arn" {
  value = var.public_ingress_enabled ? aws_acm_certificate_validation.public[0].certificate_arn : ""
}
output "public_hostname" {
  value = var.public_ingress_enabled ? var.subdomain : ""
}
output "hosted_zone_id" {
  value = var.hosted_zone_id
}
output "expires_at" {
  value = var.expires_at
}
output "resource_tags" {
  value = local.tags
}
