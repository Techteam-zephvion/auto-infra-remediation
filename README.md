# Automated Infrastructure Remediation Pipeline

An enterprise-ready, event-driven DevOps automation pipeline that detects infrastructure anomalies, extracts telemetry/logs, and automatically generates, validates, and optionally executes a remediation strategy using Large Language Models (LLMs) via LangGraph. 

## Table of Contents
- [Architecture](#architecture)
- [Enterprise Features](#enterprise-features)
- [Directory Structure](#directory-structure)
- [Setup & Deployment](#setup--deployment)
- [Testing the Workflow](#testing-the-workflow)

---

## Architecture

1. **Failure Simulation Microservice**: A Go service generating artificial CPU spikes, Memory leaks, and HTTP 500 errors.
2. **Telemetry**: The service exposes `/metrics` on port 8080. A `ServiceMonitor` targets it.
3. **Detection (Prometheus + AlertManager)**: Prometheus scrapes metrics and evaluates alerts against `PrometheusRule`. When an alert fires continuously, AlertManager hooks to a FastAPI webhook.
4. **LangGraph Agentic Orchestrator (Python)**:
   - **Parser Node**: Receives the Alert payload, queries K8s for pod context, and fetches the latest logs.
   - **Solver Node**: Employs Gemini Flash via LangGraph structured outputs (`pydantic.BaseModel`) to deterministically output a diagnosis and script.
   - **Safety Validator**: A secondary safety LLM acts as an RBAC Gatekeeper, ensuring no destructive shell scripts or K8s commands are executed (Deny list).
   - **Execution Engine**: Executes or escalates the script based on validation.

## Enterprise Features
- **Deterministic Structured Outputs**: Replaces raw text with Pydantic schemas protecting JSON parses.
- **Agentic Sandboxing / Safety Verification**: Two-LLM verification workflow preventing `rm -rf` and other malicious/dangerous remediation vectors.
- **Reproducible Local Cluster**: Uses Terraform `kind` provider to spin up an ephemeral cluster in 2 minutes for testing without impacting cloud environments.

## Directory Structure

```text
AutoInfraRemediation/
├── infra/                 # Terraform and K8s manifests
│   ├── main.tf            # KinD Cluster, Namespace, and Helm Prometheus chart
│   ├── app.yaml           # Deployment for the Go Mock Service + ServiceMonitor
│   └── alerts.yaml        # PrometheusAlerting Rules
├── service/               # Failure Simulation Go Service
│   ├── main.go
│   ├── Dockerfile
│   └── go.mod
└── webhook/               # LangGraph FastAPI Server
    ├── api.py             # Express/FastAPI interface 
    ├── graph.py           # The LangGraph State Machine
    ├── k8s_client.py      # Kubernetes python SDK wrapper
    └── requirements.txt
```

---

## Setup & Deployment

### Dependencies
- Docker installed and running
- Terraform `1.5+`
- Python `3.10+`
- `kubectl` installed
- `GEMINI_API_KEY` set

### 1. Provision Infrastructure
Deploy the KinD cluster and Prometheus Monitoring Stack:
```bash
cd infra
terraform init
terraform apply -auto-approve
```

> Note: Make sure `~/.kube/config` gets updated or point `KUBECONFIG` to the cluster.

### 2. Build and Deploy the Go Service
From the `service` directory, build the docker container, load it into kind, and apply manifests:
```bash
cd service
docker build -t auto-remediation-service:latest .
kind load docker-image auto-remediation-service:latest --name auto-remediation-cluster

# Deploy the service and alerting rules
kubectl apply -f ../infra/app.yaml
kubectl apply -f ../infra/alerts.yaml
```

### 3. Launch the LangGraph Webhook Server
The Agent requires a Python environment. Set your API Key first!
```bash
cd webhook
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/Mac: source .venv/bin/activate

pip install -r requirements.txt

# IMPORTANT: Set API Key for Gemini Flash 2.5
export GEMINI_API_KEY="your-api-key"

# Run the FastAPI server
python api.py
```
> The webhook will listen on `0.0.0.0:8000`. We configured Alertmanager in Terraform to hit `http://host.docker.internal:8000/alert`.

---

## Testing the Workflow

With both the cluster and the Webhook Server running:

1. **Port-Forward the Go Service** (if not hitting the nodePort directly):
```bash
kubectl port-forward svc/auto-remediation-service 8080:8080
```

2. **Trigger a Failure**:
Open another terminal and trigger a bug in the Go microservice.
```bash
# CPU Spike
curl http://localhost:8080/spike-cpu
```

Then monitor your webhook console for the LangGraph execution trace.

---

## 🔮 Roadmap (V2 Enhancements)
The following enterprise-readiness features are slated for the next phase of development:

1. **Human-in-the-Loop (HITL) execution**: Utilizing LangGraph breakpoints to pause before `execution_node` to await approval via a Teams/Slack webhook.
2. **State Persistence & Audit Logging**: Integrating `SqliteSaver` to the graph executor to create immutable legal audit trails of all AI decisions.
3. **PromQL Context Injection**: Giving the Parser Node API access to query 15-minute historical Metric Data (CPU/RAM trends) rather than just flat string logs.
4. **Strict Kubernetes RBAC**: Bootstrapping a dedicated `ServiceAccount` and `Role` for the Python API with extreme least-privilege scoping (e.g. explicitly denying access to `Secrets`).

3. **Watch the Flow**:
- Check the Prometheus UI (port-forward `prometheus-kube-prometheus-prometheus` svc to `9090`).
- Check the AlertManager UI (port-forward `prometheus-kube-prometheus-alertmanager` svc to `9093`).
- **Wait 1 minute**: AlertManager will fire a `severity: critical` Webhook.
- Watch your Python Webhook console! You will see:
  1. The Webhook parses the Alert
  2. Submits to LangGraph Parser Node -> Fetches Kubernetes Pod Logs
  3. Sends Logs + Alert to Solver Node -> Determines an RCA and Script
  4. Validation Node -> Checks if script touches Deny list (`rm -rf`)
  5. Execution Node -> Approves/Escalates.
