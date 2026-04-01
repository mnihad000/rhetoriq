# RhetoriQ — Infrastructure

> This document covers everything infrastructure related. Kubernetes setup, Terraform provisioning, ArgoCD GitOps, GitHub Actions CI/CD, secrets management, and environment setup. If you are deploying, scaling, or modifying infrastructure, start here.

---

## Overview

RhetoriQ's infrastructure is fully code-defined. No manual clicking in cloud consoles. No manual `kubectl apply`. Everything is reproducible from scratch with two commands:

```bash
terraform apply        # Provision all cloud resources
# ArgoCD then automatically deploys all services from Git
```

### Infrastructure Stack

| Tool | Purpose |
|---|---|
| **Terraform** | Provisions all cloud resources (EKS, RDS, ElastiCache, MSK) |
| **Kubernetes (EKS)** | Orchestrates all service deployments |
| **ArgoCD** | GitOps — auto-deploys on every merge to `main` |
| **GitHub Actions** | CI — runs tests and builds Docker images on every PR |
| **Helm** | Packages third-party services (Kafka, Elasticsearch, Neo4j) |
| **Prometheus + Grafana** | Metrics collection and dashboarding |
| **AWS Secrets Manager** | Secrets storage — no secrets in Git ever |

---

## Cloud Architecture (AWS)

```
┌─────────────────────────────────────────────────────────────┐
│                         AWS                                  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                    EKS Cluster                        │   │
│  │                                                       │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  │   │
│  │  │  Scraper    │  │   Flink     │  │   Agent     │  │   │
│  │  │  Node Group │  │  Node Group │  │  Node Group │  │   │
│  │  │  (t3.small) │  │  (t3.large) │  │  (t3.medium)│  │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  │   │
│  │                                                       │   │
│  │  ┌─────────────┐  ┌─────────────┐                    │   │
│  │  │   API       │  │  ArgoCD +   │                    │   │
│  │  │  Node Group │  │  Prometheus │                    │   │
│  │  │  (t3.small) │  │  (t3.small) │                    │   │
│  │  └─────────────┘  └─────────────┘                    │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │   AWS MSK    │  │  RDS Postgres│  │ ElastiCache  │      │
│  │   (Kafka)    │  │              │  │   (Redis)    │      │
│  └──────────────┘  └──────────────┘  └──────────────┘      │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐                        │
│  │     S3       │  │   Secrets    │                        │
│  │  (artifacts) │  │   Manager    │                        │
│  └──────────────┘  └──────────────┘                        │
└─────────────────────────────────────────────────────────────┘
```

---

## Terraform

### Structure

```
infra/
└── terraform/
    ├── main.tf           # Root module — calls all child modules
    ├── variables.tf      # Input variables
    ├── outputs.tf        # Output values (cluster endpoint, etc.)
    ├── versions.tf       # Provider version locks
    └── modules/
        ├── eks/          # EKS cluster and node groups
        ├── msk/          # AWS MSK (managed Kafka)
        ├── rds/          # RDS PostgreSQL
        ├── elasticache/  # Redis
        ├── s3/           # S3 buckets
        └── secrets/      # AWS Secrets Manager entries
```

### Prerequisites

```bash
# Install Terraform
brew install terraform  # macOS
# or
wget https://releases.hashicorp.com/terraform/1.7.0/terraform_1.7.0_linux_amd64.zip

# Install AWS CLI
brew install awscli

# Configure AWS credentials
aws configure
# Enter: AWS Access Key ID, Secret Access Key, region (us-east-1), output format (json)

# Install kubectl
brew install kubectl

# Install eksctl
brew install eksctl
```

### Initial Setup

```bash
cd infra/terraform

# Initialize Terraform — downloads providers and modules
terraform init

# Preview what will be created
terraform plan -var-file="environments/prod.tfvars"

# Apply — creates all infrastructure (~15 minutes)
terraform apply -var-file="environments/prod.tfvars"
```

### Key Resources Created

```hcl
# infra/terraform/main.tf

module "eks" {
  source          = "./modules/eks"
  cluster_name    = "rhetoriq-prod"
  cluster_version = "1.29"
  
  node_groups = {
    scrapers = {
      instance_type  = "t3.small"
      min_size       = 2
      max_size       = 5
      desired_size   = 2
    }
    flink = {
      instance_type  = "t3.large"    # Flink needs more memory for HuggingFace models
      min_size       = 1
      max_size       = 4
      desired_size   = 2
    }
    agent = {
      instance_type  = "t3.medium"
      min_size       = 1
      max_size       = 3
      desired_size   = 1
    }
    general = {
      instance_type  = "t3.small"
      min_size       = 2
      max_size       = 6
      desired_size   = 2
    }
  }
}

module "msk" {
  source         = "./modules/msk"
  cluster_name   = "rhetoriq-kafka"
  kafka_version  = "3.5.1"
  broker_count   = 3
  instance_type  = "kafka.t3.small"
  storage_gb     = 100
}

module "rds" {
  source            = "./modules/rds"
  identifier        = "rhetoriq-postgres"
  engine_version    = "15.4"
  instance_class    = "db.t3.medium"
  allocated_storage = 50
  database_name     = "rhetoriq"
}

module "elasticache" {
  source        = "./modules/elasticache"
  cluster_id    = "rhetoriq-redis"
  node_type     = "cache.t3.micro"
  num_nodes     = 1
}
```

### Environment Variables File

```hcl
# infra/terraform/environments/prod.tfvars
aws_region   = "us-east-1"
environment  = "prod"
project_name = "rhetoriq"
```

### Tearing Down

```bash
# Destroy all infrastructure
terraform destroy -var-file="environments/prod.tfvars"
# Type 'yes' to confirm
```

---

## Kubernetes

### Connect to EKS Cluster

```bash
# After terraform apply, configure kubectl
aws eks update-kubeconfig \
  --region us-east-1 \
  --name rhetoriq-prod

# Verify connection
kubectl get nodes
```

### Namespace Structure

```bash
# Create namespaces
kubectl create namespace rhetoriq        # All RhetoriQ services
kubectl create namespace monitoring      # Prometheus + Grafana
kubectl create namespace argocd          # ArgoCD
```

### Folder Structure

```
k8s/
└── manifests/
    ├── scrapers/
    │   ├── reddit-scraper.yaml
    │   ├── news-scraper.yaml
    │   ├── rss-scraper.yaml
    │   ├── gdelt-scraper.yaml
    │   └── cspan-scraper.yaml
    ├── processors/
    │   ├── flink-processor.yaml
    │   └── storage-worker.yaml
    ├── agent/
    │   └── agent.yaml
    ├── api/
    │   ├── api-deployment.yaml
    │   └── api-service.yaml
    ├── frontend/
    │   ├── frontend-deployment.yaml
    │   └── frontend-service.yaml
    └── config/
        ├── configmap.yaml
        └── hpa.yaml              # Horizontal Pod Autoscaler rules
```

### Example Deployment Manifest

```yaml
# k8s/manifests/scrapers/reddit-scraper.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: reddit-scraper
  namespace: rhetoriq
  labels:
    app: reddit-scraper
spec:
  replicas: 1
  selector:
    matchLabels:
      app: reddit-scraper
  template:
    metadata:
      labels:
        app: reddit-scraper
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8090"
    spec:
      containers:
        - name: reddit-scraper
          image: your-ecr-repo/rhetoriq-reddit-scraper:latest
          ports:
            - containerPort: 8090    # Health check port
          env:
            - name: KAFKA_BOOTSTRAP_SERVERS
              valueFrom:
                configMapKeyRef:
                  name: rhetoriq-config
                  key: KAFKA_BOOTSTRAP_SERVERS
            - name: REDDIT_CLIENT_ID
              valueFrom:
                secretKeyRef:
                  name: rhetoriq-secrets
                  key: REDDIT_CLIENT_ID
            - name: REDDIT_CLIENT_SECRET
              valueFrom:
                secretKeyRef:
                  name: rhetoriq-secrets
                  key: REDDIT_CLIENT_SECRET
          resources:
            requests:
              memory: "256Mi"
              cpu: "100m"
            limits:
              memory: "512Mi"
              cpu: "250m"
          livenessProbe:
            httpGet:
              path: /health
              port: 8090
            initialDelaySeconds: 30
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /health
              port: 8090
            initialDelaySeconds: 10
            periodSeconds: 5
      restartPolicy: Always
```

### Horizontal Pod Autoscaler

```yaml
# k8s/manifests/config/hpa.yaml

# Scale Flink processor based on CPU
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: flink-processor-hpa
  namespace: rhetoriq
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: flink-processor
  minReplicas: 1
  maxReplicas: 4
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70

# Scale agent based on Kafka consumer lag
# Requires KEDA (Kubernetes Event-Driven Autoscaling)
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: agent-scaledobject
  namespace: rhetoriq
spec:
  scaleTargetRef:
    name: agent
  minReplicaCount: 1
  maxReplicaCount: 3
  triggers:
    - type: kafka
      metadata:
        bootstrapServers: your-msk-endpoint:9092
        consumerGroup: agent-consumer
        topic: anomalies.detected
        lagThreshold: "10"    # Scale up if lag exceeds 10 messages
```

---

## ArgoCD — GitOps

ArgoCD watches the `main` branch of this repository. Every merge to `main` automatically deploys updated manifests to the EKS cluster. You never run `kubectl apply` manually in production.

### Install ArgoCD

```bash
kubectl create namespace argocd

kubectl apply -n argocd \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

# Wait for ArgoCD to be ready
kubectl wait --for=condition=available \
  --timeout=300s deployment/argocd-server -n argocd

# Get initial admin password
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d
```

### Access ArgoCD UI

```bash
# Port-forward to ArgoCD UI
kubectl port-forward svc/argocd-server -n argocd 8080:443
# Open https://localhost:8080
# Login: admin / <password from above>
```

### Create ArgoCD Application

```yaml
# argocd-app.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: rhetoriq
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://github.com/yourusername/rhetoriq.git
    targetRevision: main
    path: k8s/manifests
  destination:
    server: https://kubernetes.default.svc
    namespace: rhetoriq
  syncPolicy:
    automated:
      prune: true       # Remove resources deleted from Git
      selfHeal: true    # Revert manual kubectl changes
    syncOptions:
      - CreateNamespace=true
```

```bash
kubectl apply -f argocd-app.yaml
```

From this point forward, every merge to `main` triggers an automatic deployment.

---

## GitHub Actions — CI/CD

### Workflow

```
Push to PR branch
      │
      ▼
Run tests (pytest)
      │
      ▼
Build Docker images
      │
      ▼
Push to ECR (only on merge to main)
      │
      ▼
ArgoCD detects new image tag → deploys automatically
```

### CI Workflow

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          pip install -r backend/requirements.txt
          pip install -r backend/requirements-test.txt

      - name: Run tests
        run: |
          pytest backend/ -v --cov=backend --cov-report=xml

      - name: Upload coverage
        uses: codecov/codecov-action@v4

  build:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - uses: actions/checkout@v4

      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v4
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: us-east-1

      - name: Login to ECR
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build and push images
        run: |
          IMAGE_TAG=${{ github.sha }}
          ECR_REGISTRY=${{ secrets.ECR_REGISTRY }}
          
          services=("reddit-scraper" "news-scraper" "rss-scraper" 
                    "gdelt-scraper" "cspan-scraper" "flink-processor" 
                    "storage-worker" "agent" "api")
          
          for service in "${services[@]}"; do
            docker build -t $ECR_REGISTRY/rhetoriq-$service:$IMAGE_TAG \
              -f backend/Dockerfile.$service .
            docker push $ECR_REGISTRY/rhetoriq-$service:$IMAGE_TAG
            
            # Update the image tag in k8s manifests
            sed -i "s|rhetoriq-$service:.*|rhetoriq-$service:$IMAGE_TAG|g" \
              k8s/manifests/**/$service.yaml
          done
          
          # Commit updated manifests — ArgoCD will pick up the change
          git config user.email "ci@rhetoriq.com"
          git config user.name "RhetoriQ CI"
          git add k8s/manifests/
          git commit -m "ci: update image tags to $IMAGE_TAG"
          git push
```

---

## Secrets Management

**No secrets are ever stored in Git.** All secrets live in AWS Secrets Manager and are injected into pods at runtime via the AWS Secrets Store CSI Driver.

### Secrets Structure in AWS Secrets Manager

```
rhetoriq/prod/
├── reddit_client_id
├── reddit_client_secret
├── news_api_key
├── cspan_api_key
├── openai_api_key
├── postgres_password
├── neo4j_password
└── redis_auth_token
```

### Syncing Secrets to Kubernetes

```yaml
# k8s/manifests/config/secret-store.yaml
apiVersion: secrets-store.csi.x-k8s.io/v1
kind: SecretProviderClass
metadata:
  name: rhetoriq-secrets
  namespace: rhetoriq
spec:
  provider: aws
  parameters:
    objects: |
      - objectName: "rhetoriq/prod/openai_api_key"
        objectType: "secretsmanager"
        objectAlias: "OPENAI_API_KEY"
      - objectName: "rhetoriq/prod/reddit_client_id"
        objectType: "secretsmanager"
        objectAlias: "REDDIT_CLIENT_ID"
      # ... all other secrets
  secretObjects:
    - secretName: rhetoriq-secrets
      type: Opaque
      data:
        - objectName: "OPENAI_API_KEY"
          key: OPENAI_API_KEY
        - objectName: "REDDIT_CLIENT_ID"
          key: REDDIT_CLIENT_ID
```

---

## Observability

### Install Prometheus + Grafana

```bash
helm repo add prometheus-community \
  https://prometheus-community.github.io/helm-charts

helm install prometheus prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace \
  --set grafana.adminPassword=your_password
```

### Access Grafana

```bash
kubectl port-forward svc/prometheus-grafana 3000:80 -n monitoring
# Open http://localhost:3000
# Login: admin / your_password
```

### Key Dashboards to Build

| Dashboard | Key Panels |
|---|---|
| **Kafka Health** | Consumer lag per group, messages/sec per topic, broker disk usage |
| **Flink Processor** | Documents processed/sec, anomalies detected/hr, processing latency p99 |
| **Agent** | Investigations/hr, investigation duration p95, OpenAI cost/day, failed investigations |
| **Storage** | Write latency per database, connection pool usage, query latency p99 |
| **Infrastructure** | Pod CPU/memory per node group, HPA scaling events, pod restarts |

---

## Estimated Monthly Cost

| Resource | Instance | Est. Cost/Month |
|---|---|---|
| EKS Control Plane | — | $73 |
| EC2 Node Groups (8 nodes avg) | t3.small/medium/large | ~$180 |
| AWS MSK (Kafka) | kafka.t3.small x3 | ~$150 |
| RDS PostgreSQL | db.t3.medium | ~$60 |
| ElastiCache Redis | cache.t3.micro | ~$15 |
| S3 | — | ~$5 |
| Data Transfer | — | ~$20 |
| **Total** | | **~$500/month** |

For development, run everything locally with Docker Compose — see SERVICES.md. Only deploy to AWS when testing production behavior.
