[CmdletBinding()]
param(
    [string]$Context = "kind-rhetoriq-b6",
    [switch]$ApproveClusterMutation
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (-not $ApproveClusterMutation) { throw "Pass -ApproveClusterMutation to install platform charts." }
if ((kubectl config current-context).Trim() -ne $Context) { throw "Unexpected kubectl context." }

kubectl --context $Context create namespace rhetoriq-demo --dry-run=client -o yaml |
    kubectl --context $Context apply -f - | Out-Null
kubectl --context $Context label namespace rhetoriq-demo rhetoriq.io/trust=true pod-security.kubernetes.io/enforce=baseline --overwrite | Out-Null

helm upgrade --install cert-manager oci://quay.io/jetstack/charts/cert-manager --version v1.21.2 `
    --namespace cert-manager --create-namespace --set crds.enabled=true `
    --set image.digest=sha256:70f532fd9cfde0b09d55687200942399d89838bc2d5d5b45152eb799a15912b8 `
    --set webhook.image.digest=sha256:a60e2dac46dbb8a7f3df95c54ce941012f54c2fe022f0ee55aaa1ab40ed957ae `
    --set cainjector.image.digest=sha256:c85268c64f2e0e76684bf5fe8906caff34b82523561c6affe0fae3546bd87562 `
    --set startupapicheck.image.digest=sha256:46e75b6866359ffb5d82624f41e3ed1c70b2994982702ced547ce5edb418a8f5 `
    --atomic --wait --timeout 10m
helm upgrade --install trust-manager oci://quay.io/jetstack/charts/trust-manager --version v0.24.0 `
    --namespace trust-manager --create-namespace --set app.trust.namespace=rhetoriq-demo `
    --set image.digest=sha256:a7c1d71cad37b404738192213e3801dbf89fe797e72664b0ff0d498db35cea74 `
    --set defaultPackageImage.digest=sha256:17084a794d1e75065c9047438e2a6167907771fe78d7e4b5d4373a4b1d4e0494 `
    --atomic --wait --timeout 10m
helm upgrade --install ingress-nginx ingress-nginx --repo https://kubernetes.github.io/ingress-nginx `
    --version 4.13.3 --namespace ingress-nginx --create-namespace --atomic --wait --timeout 10m
