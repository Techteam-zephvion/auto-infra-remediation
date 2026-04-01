import asyncio
import logging
import sys
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# ── Tracing must be initialised before graph import ───────────────────────────
from tracing import setup_tracing
setup_tracing()

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from graph import build_graph
from database import setup_audit_table, insert_audit_event, update_audit_event, fetch_audit_events
from alert_tuning import get_alert_tuning
from notifications import get_notification_service

# ── Prometheus Metrics ────────────────────────────────────────────────────────
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST, CollectorRegistry, REGISTRY
import os

# Use a custom registry to avoid conflicts on reload, or check if running with reload
_USE_CUSTOM_REGISTRY = os.getenv("PROMETHEUS_MULTIPROC_DIR") is not None

if _USE_CUSTOM_REGISTRY:
    # For production with multiple workers
    from prometheus_client import CollectorRegistry
    prom_registry = CollectorRegistry()
else:
    # For development - use default registry
    prom_registry = REGISTRY

# Helper to safely create or reuse metrics
def _get_metric(metric_type, name, *args, **kwargs):
    """Get existing metric or create new one, avoiding duplicates."""
    # Check if metric already exists in registry
    for collector in list(prom_registry._collector_to_names.keys()):
        if hasattr(collector, '_name') and collector._name == name:
            return collector
    
    # Create new metric with our registry
    kwargs['registry'] = prom_registry
    return metric_type(name, *args, **kwargs)

# Remediation pipeline metrics
remediation_total = _get_metric(
    Counter,
    'remediation_workflows_total',
    'Total number of remediation workflows started',
    ['alert_type']
)

remediation_duration_seconds = _get_metric(
    Histogram,
    'remediation_duration_seconds',
    'Duration of remediation workflow execution',
    ['alert_type', 'status'],
    buckets=[5, 10, 15, 20, 30, 45, 60, 90, 120, 180]
)

safety_validation_denials = _get_metric(
    Counter,
    'safety_validation_denials_total',
    'Number of scripts denied by safety validation',
    ['alert_type', 'reason']
)

execution_failures = _get_metric(
    Counter,
    'remediation_execution_failures_total',
    'Number of failed remediation executions',
    ['alert_type']
)

active_workflows = _get_metric(
    Gauge,
    'remediation_active_workflows',
    'Number of currently active remediation workflows'
)

# LLM Cache metrics
llm_cache_hits = _get_metric(
    Counter,
    'llm_cache_hits_total',
    'Number of LLM cache hits',
    ['alert_type']
)

llm_cache_misses = _get_metric(
    Counter,
    'llm_cache_misses_total',
    'Number of LLM cache misses',
    ['alert_type']
)

llm_cache_errors = _get_metric(
    Counter,
    'llm_cache_errors_total',
    'Number of LLM cache errors'
)

llm_invocation_failures = _get_metric(
    Counter,
    'llm_invocation_failures_total',
    'Number of LLM invocation failures',
    ['node', 'error_type']
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('auto_remediation.log'),
    ],
)
logger = logging.getLogger(__name__)

# ── Lifespan Event Handler ───────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for startup and shutdown."""
    # Startup
    logger.info(f"[STARTUP] Auto-Remediation Webhook API starting at {datetime.now()}")
    _validate_security_configuration()
    await setup_audit_table()
    
    # Initialize Vault client
    from vault_client import get_vault_client, initialize_vault_secrets
    vault_client = get_vault_client()
    if vault_client:
        logger.info("[STARTUP] Vault client connected successfully")
        # Optionally initialize secrets on first run
        # initialize_vault_secrets()
    else:
        logger.warning("[STARTUP] Vault unavailable - using environment variables")
    
    # Initialize Temporal client
    from temporal_client import get_temporal_client
    temporal_client = await get_temporal_client()
    if temporal_client:
        logger.info("[STARTUP] Temporal client connected successfully")
    else:
        logger.warning("[STARTUP] Temporal unavailable - will use direct execution")
    
    yield
    
    # Shutdown
    logger.info(f"[SHUTDOWN] Auto-Remediation Webhook API shutting down at {datetime.now()}")
    
    # Close Temporal client
    from temporal_client import close_temporal_client
    await close_temporal_client()

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Auto-Remediation Webhook API",
    lifespan=lifespan
)
FastAPIInstrumentor.instrument_app(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

graph_app = build_graph()

# In-memory fallback — used when DATABASE_URL is not set
remediation_history = []

# ── Test payloads ─────────────────────────────────────────────────────────────
TEST_ALERTS = {
    "cpu_spike": {
        "receiver": "auto-remediation-webhook",
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": {
                "alertname": "HighCPUUsage",
                "namespace": "default",
                "severity": "critical",
                "app": "auto-remediation-service",
            },
            "annotations": {
                "summary": "CPU usage is above 90%",
                "description": "Pod is experiencing sustained high CPU usage above threshold",
            },
        }],
    },
    "memory_leak": {
        "receiver": "auto-remediation-webhook",
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": {
                "alertname": "HighMemoryUsage",
                "namespace": "default",
                "severity": "warning",
                "app": "auto-remediation-service",
            },
            "annotations": {
                "summary": "Memory usage exceeds 80%",
                "description": "Pod memory consumption has grown abnormally, possible memory leak",
            },
        }],
    },
    "error_rate": {
        "receiver": "auto-remediation-webhook",
        "status": "firing",
        "alerts": [{
            "status": "firing",
            "labels": {
                "alertname": "HighErrorRate",
                "namespace": "default",
                "severity": "critical",
                "app": "auto-remediation-service",
            },
            "annotations": {
                "summary": "HTTP error rate above 5%",
                "description": "Service is returning elevated 5xx errors",
            },
        }],
    },
}

# ── Security Configuration Validator ──────────────────────────────────────────
def _validate_security_configuration():
    """Validate environment configuration for security issues."""
    import os
    
    warnings = []
    errors = []
    
    # Check DATABASE_URL for default/weak credentials
    database_url = os.getenv("DATABASE_URL", "")
    if database_url:
        # Check for default postgres credentials
        if "postgres:postgres@" in database_url:
            warnings.append("DATABASE_URL contains default PostgreSQL credentials (postgres/postgres)")
        
        # Check if password is too short
        if "@" in database_url and ":" in database_url:
            try:
                # Extract password from postgresql://user:pass@host:port/db
                cred_part = database_url.split("://")[1].split("@")[0]
                if ":" in cred_part:
                    password = cred_part.split(":")[1]
                    if len(password) < 12:
                        warnings.append(f"DATABASE_URL password is weak (length: {len(password)}, recommended: 16+)")
            except:
                pass  # Ignore parsing errors
    
    # Check OLLAMA configuration
    ollama_url = os.getenv("OLLAMA_BASE_URL", "")
    if not ollama_url:
        errors.append("OLLAMA_BASE_URL not configured")
    elif ollama_url == "http://localhost:11434":
        logger.info("[CONFIG] Using default Ollama URL (localhost:11434)")
    
    ollama_model = os.getenv("OLLAMA_MODEL", "")
    if not ollama_model:
        errors.append("OLLAMA_MODEL not configured")
    
    # Check if .env file exists
    if not os.path.exists(".env") and not os.path.exists("webhook/.env"):
        warnings.append("No .env file found. See .env.example for configuration template.")
    
    # Log warnings
    if warnings:
        logger.warning("[SECURITY] Configuration warnings detected:")
        for warning in warnings:
            logger.warning(f"  [!] {warning}")
        logger.warning("[SECURITY] Review .env.example for security best practices")
    
    # Log errors and exit if critical
    if errors:
        logger.error("[SECURITY] Critical configuration errors detected:")
        for error in errors:
            logger.error(f"  [X] {error}")
        raise ValueError(f"Critical configuration errors: {', '.join(errors)}")
    
    if not warnings and not errors:
        logger.info("[SECURITY] [OK] Security configuration validation passed")


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    """Root endpoint - API information and available endpoints."""
    return {
        "service": "Auto-Remediation Webhook API",
        "version": "1.0.0",
        "status": "running",
        "description": "Automated Kubernetes infrastructure remediation using LangGraph AI pipeline",
        "endpoints": {
            "GET /": "This endpoint - API information",
            "GET /health": "Health check endpoint",
            "GET /health/ready": "Kubernetes readiness probe",
            "GET /health/live": "Kubernetes liveness probe",
            "GET /metrics": "Prometheus metrics endpoint",
            "GET /remediations": "View remediation history (last 50 events)",
            "POST /alert": "Receive AlertManager webhook notifications",
            "POST /test-alert": "Trigger test alert (body: {\"alert_type\": \"cpu_spike|memory_leak|error_rate\"})",
        },
        "features": [
            "LangGraph dual-LLM pipeline (solver + safety validator)",
            "Programmatic script pre-validation with regex deny-list",
            "LLM timeout and retry logic (30s timeout, 3 attempts)",
            "PostgreSQL audit trail for compliance",
            "OpenTelemetry distributed tracing",
        ],
        "documentation": "See ROADMAP.md for feature status and implementation plan",
    }


@app.get("/health")
async def health():
    """
    Enhanced health check with dependency verification.
    Checks: Ollama LLM, Kubernetes API, PostgreSQL database.
    """
    import os
    import httpx
    from kubernetes import client as k8s_client, config as k8s_config
    
    health_status = {
        "status": "healthy",
        "service": "auto-infra-remediation",
        "timestamp": datetime.now().isoformat(),
        "dependencies": {}
    }
    
    all_healthy = True
    
    # Check Ollama LLM
    try:
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        async with httpx.AsyncClient(timeout=5.0) as http_client:
            response = await http_client.get(f"{ollama_url}/api/tags")
            if response.status_code == 200:
                health_status["dependencies"]["ollama"] = {
                    "status": "healthy",
                    "url": ollama_url,
                    "models_available": len(response.json().get("models", []))
                }
            else:
                health_status["dependencies"]["ollama"] = {
                    "status": "unhealthy",
                    "error": f"HTTP {response.status_code}"
                }
                all_healthy = False
    except Exception as e:
        health_status["dependencies"]["ollama"] = {
            "status": "unhealthy",
            "error": str(e)
        }
        all_healthy = False
    
    # Check Kubernetes API (optional - skip if not available)
    try:
        from kubernetes import client as k8s_client, config as k8s_config
        
        # Quick check if kube config exists
        import pathlib
        kubeconfig_path = pathlib.Path.home() / ".kube" / "config"
        
        if kubeconfig_path.exists():
            health_status["dependencies"]["kubernetes"] = {
                "status": "available",
                "note": "Config found, but not tested (use K8s context for full check)"
            }
        else:
            health_status["dependencies"]["kubernetes"] = {
                "status": "not_configured",
                "note": "K8s access required for log fetching in production"
            }
    except Exception as e:
        health_status["dependencies"]["kubernetes"] = {
            "status": "not_configured",
            "note": "K8s client not available"
        }
    
    # Check PostgreSQL Database
    database_url = os.getenv("DATABASE_URL", "")
    if database_url:
        try:
            import asyncpg
            conn = await asyncpg.connect(database_url, timeout=5)
            await conn.execute("SELECT 1")
            await conn.close()
            health_status["dependencies"]["database"] = {
                "status": "healthy",
                "type": "postgresql"
            }
        except Exception as e:
            health_status["dependencies"]["database"] = {
                "status": "unhealthy",
                "error": str(e)
            }
            all_healthy = False
    else:
        health_status["dependencies"]["database"] = {
            "status": "disabled",
            "note": "Using in-memory fallback"
        }
    
    # Check Temporal workflow orchestration
    try:
        from temporal_client import check_temporal_health
        temporal_health = await check_temporal_health()
        health_status["dependencies"]["temporal"] = temporal_health
        if temporal_health["status"] not in ["healthy", "unavailable"]:
            # Don't mark as unhealthy if Temporal is just unavailable (fallback works)
            all_healthy = False
    except Exception as e:
        health_status["dependencies"]["temporal"] = {
            "status": "error",
            "error": str(e)
        }
        # Temporal errors don't block service (we have fallback)
    
    # Check Vault secret management
    try:
        from vault_client import check_vault_health
        vault_health = check_vault_health()
        health_status["dependencies"]["vault"] = vault_health
        if vault_health["status"] not in ["healthy", "unavailable"]:
            # Don't mark as unhealthy if Vault is unavailable (env vars fallback)
            pass  # Vault is optional for now
    except Exception as e:
        health_status["dependencies"]["vault"] = {
            "status": "error",
            "error": str(e)
        }
        # Vault errors don't block service (we have env var fallback)
    
    # Check Redis LLM Cache
    try:
        from cache import get_llm_cache
        llm_cache = get_llm_cache()
        if llm_cache.health_check():
            cache_stats = llm_cache.get_stats()
            health_status["dependencies"]["redis_cache"] = {
                "status": "healthy",
                "hit_rate_percent": cache_stats.get("hit_rate_percent", 0),
                "total_requests": cache_stats.get("total_requests", 0)
            }
        else:
            health_status["dependencies"]["redis_cache"] = {
                "status": "unavailable",
                "note": "Cache disabled or Redis not connected"
            }
    except Exception as e:
        health_status["dependencies"]["redis_cache"] = {
            "status": "error",
            "error": str(e)
        }
        # Cache errors don't block service (LLM calls still work)
    
    # Overall status
    if not all_healthy:
        health_status["status"] = "degraded"
    
    return health_status


@app.get("/health/ready")
async def readiness():
    """
    Readiness probe for Kubernetes.
    Returns 200 if service can handle requests, 503 otherwise.
    """
    import os
    import httpx
    
    # Critical dependency: Ollama must be available
    try:
        ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(f"{ollama_url}/api/tags")
            if response.status_code != 200:
                return {"status": "not_ready", "reason": "Ollama unavailable"}, 503
    except Exception as e:
        return {"status": "not_ready", "reason": f"Ollama error: {str(e)}"}, 503
    
    return {"status": "ready", "timestamp": datetime.now().isoformat()}


@app.get("/health/live")
async def liveness():
    """
    Liveness probe for Kubernetes.
    Returns 200 if service is alive, 503 if it should be restarted.
    """
    # Simple check - if we can respond, we're alive
    return {"status": "alive", "timestamp": datetime.now().isoformat()}


@app.get("/metrics")
async def metrics():
    """
    Prometheus metrics endpoint.
    Exposes remediation pipeline metrics for scraping.
    """
    from starlette.responses import Response
    return Response(content=generate_latest(prom_registry), media_type=CONTENT_TYPE_LATEST)


@app.get("/cache/stats")
async def get_cache_stats():
    """
    LLM cache statistics endpoint.
    Returns cache hit/miss rates, memory usage, and performance metrics.
    """
    from cache import get_llm_cache
    llm_cache = get_llm_cache()
    return llm_cache.get_stats()


@app.delete("/cache")
async def invalidate_cache(pattern: str = None):
    """
    Invalidate LLM cache entries.
    
    Query params:
        pattern: Redis key pattern to invalidate (default: all llm:response:*)
    
    Example:
        DELETE /cache              - Invalidates all cache
        DELETE /cache?pattern=llm:response:cpu_spike:*  - Invalidates cpu_spike alerts only
    """
    from cache import get_llm_cache
    llm_cache = get_llm_cache()
    deleted = llm_cache.invalidate_cache(pattern)
    return {
        "status": "success",
        "deleted_keys": deleted,
        "pattern": pattern or "llm:response:*"
    }


@app.get("/remediations")
async def get_remediations():
    """Return remediation history (PostgreSQL if configured, in-memory fallback)."""
    db_rows = await fetch_audit_events(limit=50)
    if db_rows is not None:
        return db_rows
    return list(reversed(remediation_history))


@app.post("/test-alert")
async def trigger_test_alert(body: dict):
    """Trigger a predefined test alert by type: cpu_spike | memory_leak | error_rate"""
    alert_type = body.get("alert_type", "cpu_spike")
    payload = TEST_ALERTS.get(alert_type, TEST_ALERTS["cpu_spike"])
    logger.info(f"[TEST] Triggering test alert: {alert_type}")
    asyncio.create_task(run_remediation_workflow(payload, alert_type=alert_type))
    return {
        "status": "accepted",
        "alert_type": alert_type,
        "message": f"Test alert '{alert_type}' workflow started.",
    }


@app.post("/alert")
async def receive_alert(request: Request):
    start_time = datetime.now()
    logger.info(f"[ALERT] Alert received from Alertmanager at {start_time}")

    try:
        payload = await request.json()
        asyncio.create_task(run_remediation_workflow(payload))
        return {
            "status": "accepted",
            "message": "Remediation workflow started.",
            "timestamp": start_time.isoformat(),
        }
    except Exception as e:
        logger.error(f"[ERROR] Error processing alert: {str(e)}")
        return {"status": "error", "message": f"Failed to process alert: {str(e)}"}


# ── Helper Functions ──────────────────────────────────────────────────────────
def extract_alert_info(alert_payload: dict, default_alert_type: str = "custom"):
    """
    Extract alert type and severity from AlertManager payload.
    Returns (alert_type, severity, namespace, pod_name).
    """
    try:
        if "alerts" in alert_payload and len(alert_payload["alerts"]) > 0:
            first_alert = alert_payload["alerts"][0]
            labels = first_alert.get("labels", {})
            
            # Map AlertManager alert names to our alert types
            alert_name = labels.get("alertname", "").lower()
            alert_type_mapping = {
                "highcpuusage": "cpu_spike",
                "cpuspike": "cpu_spike",
                "highmemoryusage": "memory_leak",
                "memoryleak": "memory_leak",
                "podcrashlooping": "pod_crash_loop",
                "crashloopbackoff": "pod_crash_loop",
                "diskspacelow": "disk_space_low",
                "serviceunavailable": "service_unavailable",
                "higherrorrate": "high_error_rate",
                "databaseconnectionpoolexhausted": "database_connection_pool_exhausted",
                "slowresponsetime": "slow_response_time",
            }
            
            alert_type = alert_type_mapping.get(alert_name.replace("_", "").replace("-", ""), default_alert_type)
            severity = labels.get("severity", "medium").lower()
            namespace = labels.get("namespace", "default")
            pod_name = labels.get("pod", labels.get("pod_name", "unknown"))
            
            return alert_type, severity, namespace, pod_name
        
        return default_alert_type, "medium", "default", "unknown"
    
    except Exception as e:
        logger.warning(f"[ALERT] Failed to extract alert info: {e}")
        return default_alert_type, "medium", "default", "unknown"


# ── Workflow runner ───────────────────────────────────────────────────────────
async def run_remediation_workflow(alert_payload: dict, alert_type: str = "custom"):
    workflow_start = datetime.now()
    workflow_id = f"WF-{int(workflow_start.timestamp())}"
    
    # Extract alert details
    alert_type_detected, severity, namespace, pod_name = extract_alert_info(alert_payload, alert_type)
    if alert_type == "custom":  # Override if we detected it from payload
        alert_type = alert_type_detected
    
    logger.info(f"[WORKFLOW {workflow_id}] Starting remediation workflow: type={alert_type}, severity={severity}, namespace={namespace}, pod={pod_name}")
    
    # Alert Tuning: Check if we should proceed with auto-remediation
    alert_tuning = get_alert_tuning()
    should_remediate = alert_tuning.should_auto_remediate(alert_type, severity)
    escalation_actions = alert_tuning.get_escalation_actions(alert_type, severity)
    
    if not should_remediate:
        # Suppressed during maintenance or disabled for this alert type
        logger.info(f"[WORKFLOW {workflow_id}] Auto-remediation suppressed: type={alert_type}, severity={severity}")
        logger.info(f"[WORKFLOW {workflow_id}] Escalation actions: {escalation_actions}")
        
        # Send notification but don't remediate
        notification_service = get_notification_service()
        message = "Auto-remediation suppressed by alert tuning configuration"
        notification_result = await notification_service.notify(
            escalation_actions=escalation_actions,
            alert_type=alert_type,
            severity=severity,
            message=message,
            workflow_id=workflow_id,
            namespace=namespace,
            pod_name=pod_name,
            analysis=message,
            action_taken="none_suppressed"
        )
        
        logger.info(f"[WORKFLOW {workflow_id}] Notification sent: {notification_result}")
        return
    
    logger.info(f"[WORKFLOW {workflow_id}] Auto-remediation approved, escalation actions: {escalation_actions}")
    
    # Try Temporal first, fall back to direct execution
    from temporal_client import start_remediation_workflow as start_temporal_workflow
    temporal_result = await start_temporal_workflow(workflow_id, alert_payload, alert_type)
    
    if temporal_result.get("temporal_enabled") and temporal_result.get("status") == "accepted":
        # Temporal is handling the workflow - just track metrics
        logger.info(f"[WORKFLOW {workflow_id}] Delegated to Temporal (run_id: {temporal_result.get('run_id')})")
        remediation_total.labels(alert_type=alert_type).inc()
        active_workflows.inc()
        
        # TODO: Add notification activity to temporal_workflows.py to send notifications after workflow completes
        # For now, notifications are only sent for direct execution (fallback) path
        logger.info(f"[WORKFLOW {workflow_id}] Escalation actions: {escalation_actions} (Temporal notifications not yet implemented)")
        
        return
    
    # Fallback: Direct LangGraph execution (original implementation)
    logger.warning(f"[WORKFLOW {workflow_id}] Temporal unavailable, using direct execution")
    
    # Prometheus metrics: track start
    remediation_total.labels(alert_type=alert_type).inc()
    active_workflows.inc()

    record = {
        "id": workflow_id,
        "timestamp": workflow_start.isoformat(),
        "alert_type": alert_type,
        "status": "running",
        "analysis": "",
        "script": "",
        "safety_approved": None,
        "safety_reasoning": "",
        "execution_result": "",
    }

    # Write to in-memory history (always, as a cheap fallback)
    remediation_history.append(record)
    if len(remediation_history) > 50:
        remediation_history.pop(0)

    # Write initial row to PostgreSQL
    await insert_audit_event(record)

    try:
        initial_state = {"alert_payload": alert_payload}
        step_count = 0

        for event in graph_app.stream(initial_state):
            step_count += 1
            node_name = list(event.keys())[0]
            node_state = event[node_name]
            logger.info(f"[WORKFLOW {workflow_id}] Step {step_count} — Node: {node_name}")

            if node_name == "solver" and "remediation_plan" in node_state:
                plan = node_state["remediation_plan"]
                record["analysis"] = plan.analysis
                record["script"] = plan.script
            elif node_name == "validator" and "safety_validation" in node_state:
                val = node_state["safety_validation"]
                record["safety_approved"] = val.approved
                record["safety_reasoning"] = val.reasoning
                
                # Prometheus metrics: track denials
                if not val.approved:
                    denial_reason = "pattern_match" if "prohibited command pattern" in val.reasoning else "llm_denied"
                    safety_validation_denials.labels(
                        alert_type=alert_type,
                        reason=denial_reason
                    ).inc()
                    
            elif node_name == "execution" and "execution_result" in node_state:
                record["execution_result"] = node_state["execution_result"]

        workflow_duration = (datetime.now() - workflow_start).total_seconds()
        record["status"] = "completed"
        record["duration_seconds"] = round(workflow_duration, 2)
        
        # Prometheus metrics: record duration
        remediation_duration_seconds.labels(
            alert_type=alert_type,
            status="completed"
        ).observe(workflow_duration)
        
        logger.info(f"[SUCCESS] [WORKFLOW {workflow_id}] Complete in {workflow_duration:.2f}s")

    except Exception as e:
        logger.error(f"[ERROR] [WORKFLOW {workflow_id}] Workflow failed: {str(e)}")
        record["status"] = "failed"
        record["execution_result"] = f"Error: {str(e)}"
        
        # Prometheus metrics: track failure
        execution_failures.labels(alert_type=alert_type).inc()
        
        # Record duration even for failed workflows
        workflow_duration = (datetime.now() - workflow_start).total_seconds()
        remediation_duration_seconds.labels(
            alert_type=alert_type,
            status="failed"
        ).observe(workflow_duration)
    
    finally:
        # Always decrement active workflows
        active_workflows.dec()

    # Persist final state to PostgreSQL
    await update_audit_event(record)
    
    # Send notifications based on escalation actions
    notification_service = get_notification_service()
    action_taken = "completed" if record["status"] == "completed" else "failed"
    if record.get("safety_approved") is False:
        action_taken = "blocked_unsafe"
    
    message = f"{alert_type.replace('_', ' ').title()} - {action_taken}"
    notification_result = await notification_service.notify(
        escalation_actions=escalation_actions,
        alert_type=alert_type,
        severity=severity,
        message=message,
        workflow_id=workflow_id,
        namespace=namespace,
        pod_name=pod_name,
        analysis=record.get("analysis", "No analysis available"),
        action_taken=action_taken
    )
    
    logger.info(f"[WORKFLOW {workflow_id}] Notification sent: {notification_result}")


if __name__ == "__main__":
    logger.info("[SERVER] Starting FastAPI server on 0.0.0.0:8001")
    # Pass app instance directly to avoid double import and metric duplication
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")
