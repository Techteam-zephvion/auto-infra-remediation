# Production Deployment Guide

> **AutoInfraRemediation Production Deployment**
> Complete guide for deploying AI-powered Kubernetes auto-remediation in production environments

---

## 🎯 Overview

This guide provides step-by-step instructions for deploying AutoInfraRemediation in production environments, including containerized deployment, Kubernetes manifests, monitoring setup, and operational procedures.

---

## 🏗️ Production Architecture

### **Recommended Infrastructure**
```
┌─────────────────────┐   ┌──────────────────┐   ┌────────────────────┐
│ Load Balancer       │──▶│ API Server Pods  │──▶│ Temporal Workers   │
│ (HAProxy/Ingress)   │   │ (FastAPI)        │   │ (Remediation Logic)│
└─────────────────────┘   └──────────────────┘   └────────────────────┘
           │                        │                       │
           ▼                        ▼                       ▼
┌─────────────────────┐   ┌──────────────────┐   ┌────────────────────┐
│ PostgreSQL Cluster  │   │ Redis Cluster    │   │ Temporal Server    │
│ (HA, 3 replicas)    │   │ (Master/Replica) │   │ (Workflow Engine)  │
└─────────────────────┘   └──────────────────┘   └────────────────────┘
```

### **System Requirements**

#### **Minimum Production**
- **CPU**: 8 cores (4 app + 4 DB)
- **Memory**: 16GB RAM (8GB app + 8GB DB)  
- **Storage**: 100GB SSD (50GB data + 50GB logs)
- **Network**: 1Gbps bandwidth
- **Nodes**: 3 (for HA)

#### **Recommended Production**
- **CPU**: 16 cores (8 app + 8 DB)
- **Memory**: 32GB RAM (16GB app + 16GB DB)
- **Storage**: 500GB SSD NVMe (200GB data + 300GB logs)
- **Network**: 10Gbps bandwidth  
- **Nodes**: 5 (HA + scaling)

---

## 🚀 Deployment Options

### **Option 1: Docker Compose (Simple)**

#### **1.1 Production Compose Setup**
```bash
# Clone repository
git clone <repo-url> autoinfra
cd autoinfra

# Create production environment file
cp .env.prod.example .env.prod
```

#### **1.2 Configure Environment**
```bash
# Edit .env.prod
vim .env.prod
```

```env
# Database Configuration
POSTGRES_DB=autoinfra_prod
POSTGRES_USER=autoinfra
POSTGRES_PASSWORD=<SECURE_PASSWORD_HERE>  # Generate: openssl rand -base64 32
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

# Redis Cache
REDIS_URL=redis://redis:6379/0
REDIS_PASSWORD=<REDIS_PASSWORD>  # Generate: openssl rand -base64 32

# LLM Configuration  
OLLAMA_BASE_URL=http://ollama:11434
OPENAI_API_KEY=<OPENAI_KEY_IF_FALLBACK>

# Temporal
TEMPORAL_HOST=temporal
TEMPORAL_PORT=7233

# Kubernetes
KUBECONFIG_PATH=/home/app/.kube/config
KUBERNETES_NAMESPACE=default

# Security
SECRET_KEY=<JWT_SECRET>  # Generate: openssl rand -base64 64
API_KEY_HASH=<API_KEY_HASH>

# Monitoring
JAEGER_ENABLED=true
VAULT_ENABLED=false  # Set true for production secrets

# Performance
REDIS_CACHE_TTL=3600
MAX_CONCURRENT_WORKFLOWS=10
LOG_LEVEL=INFO
```

#### **1.3 Deploy Stack**
```bash
# Start production stack
docker-compose -f docker-compose.prod.yml --env-file .env.prod up -d

# Verify deployment
docker-compose -f docker-compose.prod.yml ps

# Check logs
docker-compose -f docker-compose.prod.yml logs -f app
```

#### **1.4 Health Verification**
```bash
# API health check
curl http://localhost:8001/health

# Expected response:
{
  "status": "healthy",
  "timestamp": "2024-01-15T10:30:00Z",
  "services": {
    "database": "healthy",
    "redis": "healthy", 
    "temporal": "healthy",
    "ollama": "healthy"
  },
  "uptime": 3600,
  "version": "2.0.0"
}

# Service endpoints
curl http://localhost:8080   # Temporal UI
curl http://localhost:16686  # Jaeger UI (if enabled)
```

### **Option 2: Kubernetes (Recommended)**

#### **2.1 Prerequisites**
```bash
# Ensure cluster access
kubectl cluster-info

# Create namespace
kubectl create namespace autoinfra-prod

# Set context
kubectl config set-context --current --namespace=autoinfra-prod
```

#### **2.2 Secrets Management**
```bash
# Database secrets
kubectl create secret generic postgres-secret \
  --from-literal=username=autoinfra \
  --from-literal=password=$(openssl rand -base64 32) \
  --from-literal=database=autoinfra_prod

# Redis secret  
kubectl create secret generic redis-secret \
  --from-literal=password=$(openssl rand -base64 32)

# API secrets
kubectl create secret generic api-secret \
  --from-literal=secret-key=$(openssl rand -base64 64) \
  --from-literal=api-key-hash=$(echo -n "your-api-key" | sha256sum | cut -d' ' -f1)

# LLM API key (optional)
kubectl create secret generic llm-secret \
  --from-literal=openai-api-key="<YOUR_OPENAI_KEY>"

# Kubeconfig for job execution
kubectl create secret generic kubeconfig \
  --from-file=config=$HOME/.kube/config
```

#### **2.3 Deploy Application**
```bash
# Use provided Kubernetes manifests
kubectl apply -f infra/k8s/

# Or use Helm chart
helm install autoinfra ./helm-chart
```

---

## 🔧 Configuration Management

### **Environment-Specific Settings**

#### **Development**
```env
LOG_LEVEL=DEBUG
REDIS_CACHE_TTL=300  # 5 minutes
MAX_CONCURRENT_WORKFLOWS=3  
JAEGER_ENABLED=true
VAULT_ENABLED=false
SAFETY_VALIDATION_STRICT=false
```

#### **Staging** 
```env
LOG_LEVEL=INFO
REDIS_CACHE_TTL=1800  # 30 minutes
MAX_CONCURRENT_WORKFLOWS=5
JAEGER_ENABLED=true
VAULT_ENABLED=true
SAFETY_VALIDATION_STRICT=true
```

#### **Production**
```env
LOG_LEVEL=WARNING
REDIS_CACHE_TTL=3600  # 1 hour  
MAX_CONCURRENT_WORKFLOWS=10
JAEGER_ENABLED=true
VAULT_ENABLED=true
SAFETY_VALIDATION_STRICT=true
DATABASE_POOL_SIZE=20
WORKER_CONCURRENCY=8
```

### **Security Hardening**

#### **Container Security**
```dockerfile
# Production Dockerfile security
FROM python:3.13-slim

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Install dependencies as root
COPY requirements.prod.txt .
RUN pip install --no-cache-dir -r requirements.prod.txt

# Switch to non-root user
USER appuser
```

#### **Network Policies**
```yaml
# Restrict container network access
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: autoinfra-netpol
spec:
  podSelector:
    matchLabels:
      app: autoinfra-app
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - podSelector:
        matchLabels:
          app: prometheus  # Allow metrics scraping
    ports:
    - protocol: TCP
      port: 8001
  egress:
  - to:
    - podSelector:
        matchLabels:
          app: postgres
    ports:
    - protocol: TCP
      port: 5432
```

---

## 📊 Monitoring & Alerting

### **Prometheus Configuration**
```yaml
# prometheus.yml
global:
  scrape_interval: 15s

scrape_configs:
- job_name: 'autoinfra'
  static_configs:
  - targets: ['autoinfra-app:8001']
  scrape_interval: 10s
  metrics_path: /metrics

- job_name: 'temporal'
  static_configs:
  - targets: ['temporal-server:8080']

- job_name: 'postgres'
  static_configs:
  - targets: ['postgres-exporter:9187']

- job_name: 'redis'
  static_configs:
  - targets: ['redis-exporter:9121']
```

### **Key Metrics to Monitor**

#### **Application Metrics**
```
# Request metrics
autoinfra_http_requests_total{method, endpoint, status_code}
autoinfra_http_request_duration_seconds{method, endpoint}

# Workflow metrics  
autoinfra_workflows_started_total{workflow_type}
autoinfra_workflows_completed_total{workflow_type, status}
autoinfra_workflow_duration_seconds{workflow_type}

# Business metrics
autoinfra_alerts_processed_total{alert_type}
autoinfra_remediations_executed_total{status}
autoinfra_cache_hits_total
```

#### **Alert Rules**
```yaml
# alert_rules.yml
groups:
- name: autoinfra
  rules:
  - alert: AutoInfraAPIDown
    expr: up{job="autoinfra"} == 0
    for: 30s
    annotations:
      summary: AutoInfra API is down
      
  - alert: HighErrorRate
    expr: rate(autoinfra_http_requests_total{status_code=~"5.."}[5m]) > 0.1
    for: 1m
    annotations:
      summary: High error rate detected
      
  - alert: WorkflowFailures
    expr: rate(autoinfra_workflows_completed_total{status="failed"}[5m]) > 0.05
    for: 2m
    annotations:
      summary: Workflow failure rate too high
```

---

## 🔄 Backup & Disaster Recovery

### **Database Backup**
```bash
# Automated backup script
#!/bin/bash
# backup.sh

DB_NAME="autoinfra_prod"
BACKUP_DIR="/backups/postgresql"
RETENTION_DAYS=30

# Create backup
pg_dump -h postgres -U autoinfra -d $DB_NAME | gzip > \
  "$BACKUP_DIR/backup-$(date +%Y%m%d_%H%M%S).sql.gz"

# Cleanup old backups
find $BACKUP_DIR -name "backup-*.sql.gz" -mtime +$RETENTION_DAYS -delete

# Upload to S3 (optional)
aws s3 cp "$BACKUP_DIR/backup-$(date +%Y%m%d_%H%M%S).sql.gz" \
  s3://your-backup-bucket/autoinfra/
```

### **Disaster Recovery Procedures**

#### **Database Recovery**
```bash
# Restore from backup
gunzip < backup-20241215_020000.sql.gz | \
  psql -h postgres -U autoinfra -d autoinfra_prod

# Verify data integrity
psql -h postgres -U autoinfra -d autoinfra_prod -c \
  "SELECT COUNT(*) FROM remediation_logs;"
```

#### **Application Recovery**
```bash
# Scale down application
kubectl scale deployment autoinfra-app --replicas=0

# Update configuration if needed
kubectl edit configmap autoinfra-config

# Scale back up
kubectl scale deployment autoinfra-app --replicas=3

# Verify health
kubectl get pods -l app=autoinfra-app
curl http://autoinfra-app:8001/health
```

---

## 🚦 Operational Procedures

### **Deployment Workflow**

#### **Blue/Green Deployment**
```bash
#!/bin/bash
# deploy.sh

NEW_VERSION=$1
CURRENT_VERSION=$(kubectl get deployment autoinfra-app -o jsonpath='{.spec.template.spec.containers[0].image}' | cut -d: -f2)

echo "Deploying version $NEW_VERSION (current: $CURRENT_VERSION)"

# Deploy new version alongside current  
kubectl create -f deployment-green.yaml
kubectl wait --for=condition=available deployment/autoinfra-app-green

# Health check new version
kubectl port-forward deployment/autoinfra-app-green 8002:8001 &
curl http://localhost:8002/health || exit 1

# Switch traffic
kubectl patch service autoinfra-app -p '{"spec":{"selector":{"version":"'$NEW_VERSION'"}}}'

# Cleanup old version
kubectl delete deployment autoinfra-app-blue

echo "Deployment complete"
```

#### **Rolling Updates**
```bash
# Update image version
kubectl set image deployment/autoinfra-app \
  app=your-registry/autoinfra:2.1.0

# Monitor rollout  
kubectl rollout status deployment/autoinfra-app --timeout=300s

# Rollback if needed
kubectl rollout undo deployment/autoinfra-app
```

### **Troubleshooting Guide**

#### **Common Issues**

| Issue | Symptom | Solution |
|-------|---------|----------|
| **API Timeouts** | 504 Gateway Timeout | Check database connections, increase timeout |
| **Memory Leaks** | OOMKilled pods | Analyze memory usage, adjust resource limits |
| **Temporal Failures** | Workflows stuck | Restart Temporal server, check database |
| **LLM Timeouts** | Slow responses | Check Ollama service, use cache |
| **K8s Permission Errors** | 403 Forbidden | Verify RBAC configuration |

#### **Debug Commands**
```bash
# Check application logs
kubectl logs -f deployment/autoinfra-app --tail=100

# Check resource usage
kubectl top pods

# Database connections
kubectl exec -it deployment/postgres -- psql -U autoinfra -d autoinfra_prod -c \
  "SELECT pid, usename, application_name, state FROM pg_stat_activity;"

# Redis status  
kubectl exec -it deployment/redis -- redis-cli INFO

# Temporal workflows
curl http://temporal-server:8080/api/v1/namespaces/default/workflows
```

---

## 📋 Deployment Checklist

### **Pre-Deployment Checklist**
- [ ] **Environment**: .env.prod configured with secure passwords
- [ ] **Secrets**: All Kubernetes secrets created  
- [ ] **Storage**: PVCs created with appropriate size
- [ ] **RBAC**: Service accounts and permissions configured
- [ ] **Network**: Network policies and ingress configured
- [ ] **Monitoring**: Prometheus, Grafana dashboards ready
- [ ] **Backup**: Automated backup jobs configured
- [ ] **Testing**: Health checks and smoke tests pass

### **Post-Deployment Checklist**  
- [ ] **Health**: All endpoints return healthy status
- [ ] **Metrics**: Prometheus scraping metrics successfully  
- [ ] **Logs**: Application logs flowing to centralized system
- [ ] **Database**: Connections pool healthy, queries performing well
- [ ] **Cache**: Redis hit rate >70% after warmup
- [ ] **Workflows**: Temporal workflows executing successfully
- [ ] **Alerts**: Alert rules firing appropriately
- [ ] **Documentation**: Runbooks and procedures updated

### **Security Checklist**
- [ ] **Secrets**: No hardcoded credentials in code/config
- [ ] **RBAC** : Least privilege access configured
- [ ] **Network**: Network policies restrict unnecessary traffic  
- [ ] **Images**: Container images scanned for vulnerabilities
- [ ] **TLS**: All communication encrypted in transit
- [ ] **Audit**: Audit trail enabled and tested
- [ ] **Backup**: Backup encryption and access controls verified

---

## 💰 Cost Optimization

### **Production Costs (Monthly)**
| Component | Cost | Optimization |
|-----------|------|--------------|
| **Compute** | $60 | Use spot instances for non-critical workloads |
| **Storage** | $20 | Implement automated cleanup policies |
| **Network** | $5 | Use CDN for static content |
| **LLM APIs** | $3 | Leverage caching (93% hit rate) |
| **Monitoring** | $5 | Self-hosted Prometheus/Grafana |
| **🎯 Total** | **$93/month** | **ROI: 50x vs manual ops** |

---

**📄 Related Documentation**
- [README.md](README.md) - Complete usage guide  
- [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) - Technical architecture
- [SYSTEM_METRICS_REPORT.md](SYSTEM_METRICS_REPORT.md) - Performance metrics
- [API Documentation](api-docs.md) - REST API reference