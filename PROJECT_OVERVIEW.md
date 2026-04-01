# AutoInfraRemediation

> AI-Powered Kubernetes Auto-Remediation with Dual-LLM Safety Validation

## 🎯 Mission

Automatically detect, analyze, and remediate Kubernetes infrastructure issues using AI workflows. The system receives alerts from Prometheus, analyzes them with LLM orchestration, generates remediation scripts, validates safety, and executes approved fixes with comprehensive audit trails.

---

## 📊 Status

| Metric | Value |
|--------|-------|
| **Production Ready** | ✅ 10/10 |
| **Phase Completion** | ✅ 100% (6/6 phases) |
| **Test Coverage** | 102 tests, 100% pass rate |
| **Performance** | 93% faster with cache (2s vs 30s) |
| **Cost** | $93/month ($90 infra + $3 LLM) |
| **Version** | 2.0.0 |

📄 **Full Details**: [System Metrics Report](SYSTEM_METRICS_REPORT.md) | [Task Completion](PENDING_TASKS.md)

---

## 🏗️ Architecture

```
┌─────────────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────┐
│ Prometheus      │────▶│ FastAPI API  │────▶│ Temporal    │────▶│ LangGraph│
│ AlertManager    │     │ (11 endpoints│     │ Workflow    │     │ Pipeline │
└─────────────────┘     └──────────────┘     └─────────────┘     └──────────┘
                               │                    │                   │
                               ▼                    ▼                   ▼
                        ┌─────────────┐      ┌──────────┐      ┌────────────┐
                        │ PostgreSQL  │      │ Redis    │      │ ChromaDB   │
                        │ Audit Trail │      │ Cache    │      │ RAG/Vector │
                        └─────────────┘      └──────────┘      └────────────┘
                               │                    
                               ▼                    
                        ┌─────────────┐      ┌──────────┐      ┌────────────┐
                        │ Jaeger      │      │ Vault    │      │ K8s Jobs   │
                        │ Tracing     │      │ Secrets  │      │ Sandboxing │
                        └─────────────┘      └──────────┘      └────────────┘
```

### Core Services

| Service | Purpose | Port/URL |
|---------|---------|----------|
| **FastAPI** | Alert webhook & API (11 endpoints) | :8001 |
| **PostgreSQL** | Audit trail storage | :5432 |
| **Redis** | LLM response cache (93% faster) | :6379 |
| **ChromaDB** | Vector DB for RAG semantic search | :8000 |
| **Temporal** | Workflow orchestration | :7233, UI :8080 |
| **Jaeger** | Distributed tracing | UI :16686 |
| **Vault** | Secret management | :8200 |

---

## 🔧 Key Features

### LangGraph Pipeline (4 Nodes)
1. **Parser**: Extracts alerts, fetches K8s pod logs
2. **Solver**: LLM analysis with RAG context + cache check (93% faster on hit)
3. **Validator**: Dual-layer safety (13 regex patterns + AI review)
4. **Executor**: Sandboxed K8s Jobs with 7-layer security

### Intelligence Layer
- **Multi-Model LLM Router**: qwen2.5:3b → llama3.1:8b → GPT-4 with circuit breakers
- **Redis Cache**: 1h TTL, 2.05s vs 30s (93% reduction)
- **RAG/ChromaDB**: 384-dim embeddings, semantic search, top-3 similar cases

### Production Hardening
- **Temporal Workflows**: Exactly-once execution, crash recovery (<5s)
- **Vault Secrets**: KV v2 engine, graceful fallback (99.9% uptime)
- **Testing**: 102 tests (unit, integration, E2E) - 100% pass rate

### Operational Maturity
- **Alert Tuning**: 8 types (CPU 80%, memory 85%, etc.), Slack/PagerDuty integration
- **DB Tracing**: OpenTelemetry spans, slow query detection (>500ms)
- **Sandboxing**: Ephemeral K8s Jobs (NetworkPolicy, 256Mi RAM, 60s timeout)

### Security Features
- K8s RBAC (read-only + Job creation only)
- 13 DENY patterns (rm -rf, fork bombs, etc.)
- Non-root execution, read-only FS, no capabilities
- All workflows audited to PostgreSQL
- 7-layer sandboxing (NetworkPolicy, resource limits, timeout, auto-cleanup)

---

## 🛠️ Tech Stack

**Core**: Python 3.13, FastAPI, LangGraph, LangChain, Ollama  
**Data**: PostgreSQL 16, Redis 7.2, ChromaDB 0.4  
**Orchestration**: Temporal 1.22, Kubernetes  
**Observability**: Prometheus (18+ metrics), Jaeger, OpenTelemetry  
**Security**: Vault 1.15, NetworkPolicy, RBAC  
**AI/ML**: qwen2.5:3b, llama3.1:8b, GPT-4, sentence-transformers

---

## 🚀 Quick Start

```powershell
# 1. Start all services (PostgreSQL, Redis, ChromaDB, Temporal, Jaeger, Vault)
cd AutoInfraRemediation/webhook
docker-compose up -d

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env with your settings

# 4. Pull LLM model & start API
ollama pull qwen2.5:3b
python api.py

# 5. Test the system
curl http://localhost:8001/health
curl -X POST http://localhost:8001/test-alert \
  -H "Content-Type: application/json" \
  -d '{"alert_type":"memory_leak"}'
```

### Service URLs

- **API**: http://localhost:8001
- **Health**: http://localhost:8001/health  
- **Metrics**: http://localhost:8001/metrics (18+ metrics)
- **Jaeger Tracing**: http://localhost:16686
- **Temporal UI**: http://localhost:8080
- **Vault UI**: http://localhost:8200

---

## 📈 Observability

### Prometheus Metrics (18+)
- **Workflow**: workflows_total, duration_seconds, safety_denials, execution_failures
- **Cache**: requests_total, hits_total, misses_total, hit_rate
- **LLM Router**: requests (by model), errors, latency, fallbacks, circuit_breaker_state, cost
- **RAG**: queries_total, hits/misses (by alert_type), cases_retrieved, errors

### Alerting Rules
- **HighSafetyValidationDenialRate**: >0.5 denials/sec for 5min
- **HighRemediationFailureRate**: >0.3 failures/sec for 5min
- **SlowRemediationWorkflows**: p95 latency >60s for 10min
- **NoRemediationWorkflows**: No workflows for 30min

### Distributed Tracing
- All 4 LangGraph nodes traced with OpenTelemetry
- Database operations instrumented (INSERT, UPDATE, SELECT)
- Slow query detection (>500ms threshold)
- View in Jaeger UI: http://localhost:16686

---

## 📁 Project Structure

```
AutoInfraRemediation/
├── webhook/                        # Main application
│   ├──  api.py                      # FastAPI server (11 endpoints)
│   ├── graph.py                    # LangGraph pipeline (4 nodes)
│   ├── database.py                 # PostgreSQL + tracing
│   ├── k8s_client.py               # Kubernetes integration
│   ├── temporal_*.py               # Workflow orchestration (4 files)
│   ├── vault_client.py             # Secret management
│   ├── cache.py                    # Redis LLM cache
│   ├── llm_router.py               # Multi-model fallback
│   ├── knowledge_base.py           # ChromaDB RAG
│   ├── embeddings.py               # Text embeddings
│   ├── alert_tuning.py             # Alert thresholds
│   ├── notifications.py            # Slack/PagerDuty
│   ├── job_executor.py             # Sandbox orchestration
│   ├── docker-compose.yml          # 7 services
│   ├── requirements.txt            # Python deps
│   ├── test_*.py                   # 5 test files (102 tests total)
│   └── tests/                      # Test suite (4 files)
│
├── infra/                          # Infrastructure as Code
│   ├── main.tf                     # GKE + Prometheus (Terraform)
│   ├── webhook-*.yaml              # K8s manifests (3 files)
│   ├── servicemonitor.yaml         # Prometheus + alerting rules
│   ├── alerts.yaml                 # AlertManager config
│   ├── alert-thresholds.yaml       # Alert tuning (8 types)
│   ├── sandbox-*.yaml              # Security templates (2 files)
│   └── app.yaml                    # Legacy config
│
├── service/                        # Failure simulator (Go)
│   ├── main.go                     # Test endpoints
│   └── Dockerfile
│
├── SYSTEM_METRICS_REPORT.md        # Comprehensive metrics (942 lines)
├── PENDING_TASKS.md                # Task tracking (all complete)
├── ROADMAP.md                      # Development history
└── PROJECT_OVERVIEW.md             # This file
```

---

## 🔄 Workflow Example

**Scenario**: Memory leak detected in production pod

1. **Prometheus** detects memory >80%, fires alert to AlertManager
2. **AlertManager** routes to FastAPI webhook (`POST /alert`)
3. **Temporal** starts workflow, wraps LangGraph pipeline
4. **Parser** node: Fetches pod logs from Kubernetes API
5. **Solver** node:
   - Checks **Redis cache** for identical alert (93% faster if hit)
   - Performs **RAG search** in ChromaDB for similar cases
   - **LLM router** generates remediation (qwen2.5:3b primary)
6. **Validator** node:
   - Regex check: 13 DENY patterns
   - AI review: Second LLM validates safety
7. **Executor** node:
   - Creates ephemeral K8s Job (sandboxed)
   - Executes: `kubectl rollout restart deployment my-app`
   - Captures output, cleans up Job
8. **PostgreSQL**: Stores audit event with full trace
9. **Jaeger**: Records distributed trace with all spans
10. **Notifications**: Sends Slack message with results

**Duration**: 2.05s (cache hit) or 30-40s (cache miss)

---

## 📚 Documentation

- **[SYSTEM_METRICS_REPORT.md](SYSTEM_METRICS_REPORT.md)**: Comprehensive metrics, costs, performance analysis (942 lines)
- **[PENDING_TASKS.md](PENDING_TASKS.md)**: All 6 phases complete - breakdown of features implemented
- **[ROADMAP.md](ROADMAP.md)**: Development history and completed features
- **API Docs**: http://localhost:8001 (FastAPI auto-generated, Swagger UI)

---

## 🚢 Deployment

### Local Development
```powershell
docker-compose up -d        # All services
python api.py               # API server
python worker.py            # Temporal worker (separate terminal)
```

### Production (GKE)
```bash
cd infra
terraform init
terraform plan
terraform apply             # Creates GKE Autopilot + Prometheus

kubectl apply -f webhook-rbac.yaml
kubectl apply -f webhook-deployment.yaml
kubectl apply -f servicemonitor.yaml
kubectl apply -f alerts.yaml
```

### Environment Variables
See [webhook/.env.example](webhook/.env.example) for full configuration template.

**Key vars**: `DATABASE_URL`, `TEMPORAL_ADDRESS`, `VAULT_ADDR`, `REDIS_URL`, `CHROMADB_URL`, `OLLAMA_BASE_URL`

---

## 📞 Project Info

**Version**: 2.0.0  
**Status**: ✅ Production Ready (10/10)  
**Last Updated**: April 1, 2026  
**License**: Proprietary - Zephvion © 2026

**Highlights**:
- ⚡ 93% latency reduction (cache hits: 2s vs 30s)
- 💰 $93/month total cost
- 🔒 7-layer security isolation (sandboxing)
- ✅ 102 tests (100% pass rate)
- 📊 18+ Prometheus metrics
- 🔄 Exactly-once execution (Temporal)
- 🛡️ Dual-LLM safety validation
- 🧠 RAG semantic search (ChromaDB)
