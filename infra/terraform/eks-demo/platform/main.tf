data "terraform_remote_state" "foundation" {
  backend = "s3"
  config = {
    bucket       = var.state_bucket
    key          = var.foundation_state_key
    region       = var.region
    encrypt      = true
    use_lockfile = true
  }
}

data "aws_eks_cluster" "demo" { name = data.terraform_remote_state.foundation.outputs.cluster_name }
data "aws_eks_cluster_auth" "demo" { name = data.terraform_remote_state.foundation.outputs.cluster_name }

provider "aws" { region = var.region }
provider "kubernetes" {
  host                   = data.aws_eks_cluster.demo.endpoint
  cluster_ca_certificate = base64decode(data.aws_eks_cluster.demo.certificate_authority[0].data)
  token                  = data.aws_eks_cluster_auth.demo.token
}
provider "helm" {
  kubernetes = {
    host                   = data.aws_eks_cluster.demo.endpoint
    cluster_ca_certificate = base64decode(data.aws_eks_cluster.demo.certificate_authority[0].data)
    token                  = data.aws_eks_cluster_auth.demo.token
  }
}

resource "kubernetes_namespace_v1" "platform" {
  for_each = toset(["cert-manager", "trust-manager"])
  metadata {
    name = each.value
    labels = {
      "app.kubernetes.io/managed-by"       = "terraform"
      "pod-security.kubernetes.io/enforce" = "baseline"
      "pod-security.kubernetes.io/warn"    = "restricted"
      "pod-security.kubernetes.io/audit"   = "restricted"
    }
  }
}

# Strict VPC CNI policy mode also covers platform pods. These broad platform
# policies keep DNS, admission webhooks, CSI, and controllers available while
# the application namespace remains default-deny with explicit service flows.
resource "kubernetes_network_policy_v1" "platform_allow_all" {
  for_each = toset(["kube-system", "cert-manager", "trust-manager"])
  metadata {
    name      = "rhetoriq-platform-availability"
    namespace = each.value
  }
  spec {
    pod_selector {}
    policy_types = ["Ingress", "Egress"]
    ingress {}
    egress {}
  }
  depends_on = [kubernetes_namespace_v1.platform]
}

resource "helm_release" "cert_manager" {
  name             = "cert-manager"
  namespace        = "cert-manager"
  create_namespace = false
  repository       = "oci://quay.io/jetstack/charts"
  chart            = "cert-manager"
  version          = var.cert_manager_version
  atomic           = true
  timeout          = 600
  values = [yamlencode({
    crds = { enabled = true }
    image = { digest = "sha256:70f532fd9cfde0b09d55687200942399d89838bc2d5d5b45152eb799a15912b8" }
    webhook = { image = { digest = "sha256:a60e2dac46dbb8a7f3df95c54ce941012f54c2fe022f0ee55aaa1ab40ed957ae" } }
    cainjector = { image = { digest = "sha256:c85268c64f2e0e76684bf5fe8906caff34b82523561c6affe0fae3546bd87562" } }
    startupapicheck = { image = { digest = "sha256:46e75b6866359ffb5d82624f41e3ed1c70b2994982702ced547ce5edb418a8f5" } }
  })]
  depends_on       = [kubernetes_network_policy_v1.platform_allow_all]
}

resource "helm_release" "trust_manager" {
  name             = "trust-manager"
  namespace        = "trust-manager"
  create_namespace = false
  repository       = "oci://quay.io/jetstack/charts"
  chart            = "trust-manager"
  version          = var.trust_manager_version
  atomic           = true
  timeout          = 600
  values = [yamlencode({
    app = { trust = { namespace = local.namespace } }
    image = { digest = "sha256:a7c1d71cad37b404738192213e3801dbf89fe797e72664b0ff0d498db35cea74" }
    defaultPackageImage = { digest = "sha256:17084a794d1e75065c9047438e2a6167907771fe78d7e4b5d4373a4b1d4e0494" }
  })]
  depends_on       = [helm_release.cert_manager, kubernetes_namespace_v1.rhetoriq]
}

resource "helm_release" "load_balancer_controller" {
  name       = "aws-load-balancer-controller"
  namespace  = "kube-system"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = var.load_balancer_controller_chart_version
  atomic     = true
  timeout    = 600
  values = [yamlencode({
    clusterName    = data.terraform_remote_state.foundation.outputs.cluster_name
    region         = var.region
    vpcId          = data.terraform_remote_state.foundation.outputs.vpc_id
    serviceAccount = { create = true, name = "aws-load-balancer-controller" }
    image = { tag = "v3.5.0@sha256:298acdff5a571731276aaea3d5cc450a264e4ad710a5bddf3e518f68a3f9f6cb" }
  })]
  depends_on = [kubernetes_network_policy_v1.platform_allow_all]
}

resource "aws_eks_pod_identity_association" "load_balancer_controller" {
  cluster_name    = data.terraform_remote_state.foundation.outputs.cluster_name
  namespace       = "kube-system"
  service_account = "aws-load-balancer-controller"
  role_arn        = data.terraform_remote_state.foundation.outputs.load_balancer_controller_role_arn
  depends_on      = [helm_release.load_balancer_controller]
}

resource "kubernetes_storage_class_v1" "gp3" {
  metadata { name = "gp3-encrypted" }
  storage_provisioner    = "ebs.csi.aws.com"
  reclaim_policy         = "Delete"
  volume_binding_mode    = "WaitForFirstConsumer"
  allow_volume_expansion = true
  parameters = merge({
    type      = "gp3"
    encrypted = "true"
    fsType    = "ext4"
    }, {
    for index, key in sort(keys(data.terraform_remote_state.foundation.outputs.resource_tags)) :
    "tagSpecification_${index + 1}" => "${key}=${data.terraform_remote_state.foundation.outputs.resource_tags[key]}"
  })
}

resource "kubernetes_namespace_v1" "rhetoriq" {
  metadata {
    name = local.namespace
    labels = {
      "rhetoriq.io/trust"                  = "true"
      "pod-security.kubernetes.io/enforce" = "baseline"
      "app.kubernetes.io/name"             = "rhetoriq"
    }
  }
}

resource "helm_release" "rhetoriq" {
  count            = var.deploy_application ? 1 : 0
  name             = "rhetoriq"
  namespace        = local.namespace
  create_namespace = false
  chart            = "${path.module}/../../../../deploy/helm/rhetoriq"
  atomic           = true
  timeout          = 1800
  values = [
    file("${path.module}/../../../../deploy/helm/rhetoriq/values-eks-demo.yaml"),
    yamlencode({
      images = {
        backend  = { repository = data.terraform_remote_state.foundation.outputs.ecr_repositories.backend, digest = var.backend_digest }
        b5       = { repository = data.terraform_remote_state.foundation.outputs.ecr_repositories.b5, digest = var.b5_digest }
        flink    = { repository = data.terraform_remote_state.foundation.outputs.ecr_repositories.flink, digest = var.flink_digest }
        frontend = { repository = data.terraform_remote_state.foundation.outputs.ecr_repositories.frontend, digest = var.frontend_digest }
      }
      ingress = {
        enabled           = var.public_ingress_enabled
        className         = "alb"
        host              = data.terraform_remote_state.foundation.outputs.public_hostname
        acmCertificateArn = data.terraform_remote_state.foundation.outputs.public_certificate_arn
        allowedCidrs      = join(",", var.allowed_cidrs)
        loadBalancerName  = local.alb_name
        awsResourceTags   = join(",", [for key in sort(keys(data.terraform_remote_state.foundation.outputs.resource_tags)) : "${key}=${data.terraform_remote_state.foundation.outputs.resource_tags[key]}"])
      }
      network  = { ingressCidrs = [data.terraform_remote_state.foundation.outputs.vpc_cidr] }
      smoke    = { enabled = var.smoke_enabled }
      evidence = { enabled = var.evidence_enabled }
    })
  ]
  depends_on = [
    helm_release.cert_manager,
    helm_release.trust_manager,
    kubernetes_storage_class_v1.gp3,
    kubernetes_namespace_v1.rhetoriq,
    aws_eks_pod_identity_association.load_balancer_controller,
  ]
}

data "aws_lb" "rhetoriq" {
  count      = var.deploy_application && var.public_ingress_enabled ? 1 : 0
  name       = local.alb_name
  depends_on = [helm_release.rhetoriq]
}

resource "aws_route53_record" "application" {
  count   = var.deploy_application && var.public_ingress_enabled ? 1 : 0
  zone_id = data.terraform_remote_state.foundation.outputs.hosted_zone_id
  name    = data.terraform_remote_state.foundation.outputs.public_hostname
  type    = "A"
  alias {
    name                   = data.aws_lb.rhetoriq[0].dns_name
    zone_id                = data.aws_lb.rhetoriq[0].zone_id
    evaluate_target_health = true
  }
}
