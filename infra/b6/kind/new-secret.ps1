[CmdletBinding()]
param(
    [string]$Context = "kind-rhetoriq-b6",
    [switch]$ApproveSecretUpload
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (-not $ApproveSecretUpload) { throw "Pass -ApproveSecretUpload only after approving the local Kubernetes secret write." }
if ((kubectl config current-context).Trim() -ne $Context) { throw "Unexpected kubectl context." }

$required = @(
    "RHETORIQ_POSTGRES_USER", "RHETORIQ_POSTGRES_PASSWORD", "RHETORIQ_POSTGRES_DB",
    "RHETORIQ_ELASTICSEARCH_PASSWORD", "RHETORIQ_NEO4J_PASSWORD",
    "RHETORIQ_REDIS_PASSWORD", "RHETORIQ_SEARXNG_SECRET"
)
$values = [ordered]@{}
foreach ($name in $required) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ([string]::IsNullOrWhiteSpace($value)) { throw "Required process environment variable is missing: $name" }
    $values[$name] = $value
}
if ($values.RHETORIQ_POSTGRES_DB -notmatch "test") {
    throw "The demo smoke harness requires a disposable PostgreSQL database name containing 'test'."
}

$user = [uri]::EscapeDataString($values.RHETORIQ_POSTGRES_USER)
$password = [uri]::EscapeDataString($values.RHETORIQ_POSTGRES_PASSWORD)
$database = [uri]::EscapeDataString($values.RHETORIQ_POSTGRES_DB)
$geminiKey = [Environment]::GetEnvironmentVariable("RHETORIQ_GEMINI_API_KEY")
$groqKey = [Environment]::GetEnvironmentVariable("RHETORIQ_GROQ_API_KEY")
if ($null -eq $geminiKey) { $geminiKey = "" }
if ($null -eq $groqKey) { $groqKey = "" }
$plain = [ordered]@{
    "postgres-user" = $values.RHETORIQ_POSTGRES_USER
    "postgres-password" = $values.RHETORIQ_POSTGRES_PASSWORD
    "postgres-db" = $values.RHETORIQ_POSTGRES_DB
    "database-url" = "postgresql://${user}:${password}@postgres:5432/${database}?sslmode=verify-full&sslrootcert=/etc/rhetoriq/ca/ca.crt"
    "elasticsearch-password" = $values.RHETORIQ_ELASTICSEARCH_PASSWORD
    "neo4j-password" = $values.RHETORIQ_NEO4J_PASSWORD
    "neo4j-auth" = "neo4j/$($values.RHETORIQ_NEO4J_PASSWORD)"
    "redis-password" = $values.RHETORIQ_REDIS_PASSWORD
    "searxng-secret" = $values.RHETORIQ_SEARXNG_SECRET
    "gemini-api-key" = $geminiKey
    "groq-api-key" = $groqKey
}
$encoded = [ordered]@{}
foreach ($entry in $plain.GetEnumerator()) {
    $encoded[$entry.Key] = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($entry.Value))
}
$secret = [ordered]@{
    apiVersion = "v1"
    kind = "Secret"
    metadata = @{ name = "rhetoriq-secrets"; namespace = "rhetoriq-demo" }
    type = "Opaque"
    data = $encoded
}
$secret | ConvertTo-Json -Depth 8 -Compress | kubectl --context $Context apply -f - | Out-Null
Write-Host "Applied Secret rhetoriq-demo/rhetoriq-secrets without printing its data."
