---
# AutoInfraRemediation — Roadmap

## Pending Upgrades

- [ ] **Temporal Workflow Durability**
  Wrap the LangGraph pipeline in a Temporal workflow for exactly-once execution and crash recovery. If the webhook crashes mid-pipeline, the alert is currently lost. Already have Temporal expertise from the ETL project — realistic upgrade.

- [ ] **HashiCorp Vault Integration**
  Replace env var secrets with a Vault dev server. Currently docs claim Vault support but code uses `.env`. Even a basic integration makes the claim real.

---

## Completed

- [x] LangGraph pipeline with dual-LLM safety validator
- [x] Prometheus + AlertManager → FastAPI webhook integration
- [x] Structured Pydantic outputs for remediation scripts
- [x] **GKE Autopilot** — replaced KinD with GKE Autopilot (`infra/main.tf`); google provider ~5.0, Workload Identity enabled; AlertManager webhook URL is now a variable; KinD config preserved as `kind.tf.local`
- [x] **OpenTelemetry tracing** — `webhook/tracing.py` sets up OTLP HTTP export to Jaeger; all 4 graph nodes (parser, solver, validator, execution) create child spans with alert/k8s/LLM attributes; FastAPI middleware traces all HTTP requests; toggle via `OTEL_ENABLED` env var
- [x] **PostgreSQL audit trail** — `webhook/database.py` writes every workflow to `audit_events` table (asyncpg); `GET /remediations` returns DB rows when `DATABASE_URL` is set, falls back to in-memory list; `webhook/docker-compose.yml` provides Jaeger + PostgreSQL for local dev
