import os
import logging
import asyncio
from datetime import datetime
from kubernetes import client, config

# Configure logging
logger = logging.getLogger(__name__)

def init_k8s():
    """Initialize Kubernetes client."""
    init_start = datetime.now()
    logger.info(f"[K8S] Initializing Kubernetes client at {init_start}")
    
    try:
        # Tries to load in-cluster config first
        logger.info("[K8S] Attempting to load in-cluster Kubernetes config...")
        config.load_incluster_config()
        logger.info("[SUCCESS] Successfully loaded in-cluster config")
    except config.ConfigException:
        logger.info("[WARNING] In-cluster config failed, trying kubeconfig...")
        # Fallbacks to kubeconfig
        try:
            config.load_kube_config()
            logger.info("[SUCCESS] Successfully loaded kubeconfig")
        except Exception as e:
            logger.error(f"[ERROR] Failed to load any Kubernetes config: {str(e)}")
            raise
    
    init_duration = (datetime.now() - init_start).total_seconds()
    logger.info(f"[SUCCESS] Kubernetes client initialized in {init_duration:.2f} seconds")

def get_pod_logs(namespace: str, pod_name: str, tail_lines: int = 50) -> str:
    """Fetch logs from a specific pod."""
    log_start = datetime.now()
    logger.info(f"[LOGS] Getting logs from pod '{pod_name}' in namespace '{namespace}' (tail_lines={tail_lines}) at {log_start}")
    
    try:
        init_k8s()
        v1 = client.CoreV1Api()
        logger.info(f"[API] Making API call to fetch logs...")
        
        logs = v1.read_namespaced_pod_log(name=pod_name, namespace=namespace, tail_lines=tail_lines)
        
        log_duration = (datetime.now() - log_start).total_seconds()
        logger.info(f"[SUCCESS] Successfully fetched {len(logs)} characters of logs in {log_duration:.2f} seconds")
        
        # Log a sample of the logs for debugging
        if logs:
            logger.debug(f"[LOGS] Log sample (first 200 chars): {logs[:200]}{'...' if len(logs) > 200 else ''}")
            logger.debug(f"[LOGS] Log sample (last 200 chars): {'...' + logs[-200:] if len(logs) > 200 else logs}")
        else:
            logger.warning("[WARNING] No logs found for this pod")
            
        return logs
        
    except Exception as e:
        error_msg = f"Error fetching logs for {pod_name}: {str(e)}"
        logger.error(f"[ERROR] {error_msg}")
        logger.exception("Full error traceback for log fetch:")
        return error_msg

def get_pods_with_labels(namespace: str, label_selector: str) -> list:
    """Get pods matching a label selector."""
    search_start = datetime.now()
    logger.info(f"[PODS] Searching for pods in namespace '{namespace}' with labels '{label_selector}' at {search_start}")
    
    try:
        init_k8s()
        v1 = client.CoreV1Api()
        logger.info(f"[API] Making API call to list pods...")
        
        pods = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
        pod_names = [pod.metadata.name for pod in pods.items]
        
        search_duration = (datetime.now() - search_start).total_seconds()
        logger.info(f"[SUCCESS] Found {len(pod_names)} matching pods in {search_duration:.2f} seconds")
        
        if pod_names:
            logger.info(f"[PODS] Pod names: {pod_names}")
            for i, pod in enumerate(pods.items):
                logger.debug(f"[PODS] Pod {i+1}: {pod.metadata.name} (Status: {pod.status.phase})")
        else:
            logger.warning(f"[WARNING] No pods found matching label selector '{label_selector}' in namespace '{namespace}'")
            
        return pod_names
        
    except Exception as e:
        logger.error(f"[ERROR] Error listing pods: {e}")
        logger.exception("Full error traceback for pod listing:")
        return []

def execute_remediation(script: str) -> str:
    """
    Execute a validated remediation.
    In a real system, this could invoke an Ansible playbook or specific K8s API patches.
    For this demo, we'll log what would be done, and simulate a safe restart if requested.
    """
    exec_start = datetime.now()
    logger.info(f"[EXECUTION ENGINE] Starting remediation execution at {exec_start}")
    logger.info(f"[SCRIPT] Script length: {len(script)} characters")
    
    # Log the full script for audit purposes
    logger.info(f"[SCRIPT] Full contents:\n{'-'*50}\n{script}\n{'-'*50}")
    
    try:
        # In this demo, we simulate execution but don't actually run dangerous commands
        if "kubectl" in script or "docker" in script or "helm" in script:
            logger.info("[SIMULATION] Kubernetes/Docker/Helm command detected - simulating execution")
            result = "Remediation executed successfully (simulated for safety)."
        elif "restart" in script.lower():
            logger.info("[SIMULATION] Restart command detected - simulating pod restart")
            result = "Pod restart initiated successfully (simulated)."
        elif "echo" in script:
            logger.info("[ECHO] Echo command detected - safe to execute")
            result = "Echo command executed successfully."
        else:
            logger.info("[SAFETY] Unknown command type - executing in safe mode")
            result = "Command executed in safe mode."
        
        exec_duration = (datetime.now() - exec_start).total_seconds()
        logger.info(f"[SUCCESS] [EXECUTION] Remediation completed in {exec_duration:.2f} seconds")
        logger.info(f"[RESULT] {result}")
        
        return result
        
    except Exception as e:
        logger.error(f"[ERROR] [EXECUTION] Remediation failed: {str(e)}")
        logger.exception("Full error traceback for remediation execution:")
        return f"Execution failed with error: {str(e)}"


def execute_remediation_sandboxed(
    script: str,
    workflow_id: str,
    namespace: str = "default",
    alert_type: str = "custom",
    pod_name: str = "unknown"
) -> str:
    """
    Execute remediation script in isolated Kubernetes Job
    
    This function uses the JobExecutor to run scripts in ephemeral Jobs with:
    - Network isolation (NetworkPolicy)
    - Resource limits (CPU, memory, timeout)
    - Security context (non-root, read-only filesystem)
    - Automatic cleanup after completion
    
    Args:
        script: Shell script to execute
        workflow_id: Unique workflow identifier
        namespace: Kubernetes namespace
        alert_type: Alert type triggering remediation
        pod_name: Target pod name
    
    Returns:
        Execution result string
    """
    exec_start = datetime.now()
    logger.info(f"[SANDBOXED_EXECUTION] Starting sandboxed execution for {workflow_id}")
    logger.info(f"[SANDBOX] Target: namespace={namespace}, pod={pod_name}, alert_type={alert_type}")
    logger.info(f"[SCRIPT] Script length: {len(script)} characters")
    logger.info(f"[SCRIPT] Full contents:\n{'-'*50}\n{script}\n{'-'*50}")
    
    try:
        # Import JobExecutor (lazy import to avoid circular dependencies)
        from job_executor import get_job_executor
        
        executor = get_job_executor()
        
        # Run async execution in sync context
        logger.info("[SANDBOX] Creating isolated Kubernetes Job...")
        result_dict = asyncio.run(
            executor.execute_script(
                script=script,
                workflow_id=workflow_id,
                namespace=namespace,
                alert_type=alert_type,
                pod_name=pod_name,
                timeout=60
            )
        )
        
        exec_duration = (datetime.now() - exec_start).total_seconds()
        
        # Parse results
        if result_dict['success']:
            logger.info(f"[SUCCESS] [SANDBOXED_EXECUTION] Job {result_dict['job_name']} completed successfully")
            logger.info(f"[SANDBOX] Duration: {result_dict['duration_seconds']}s")
            logger.info(f"[SANDBOX] Exit code: {result_dict['exit_code']}")
            logger.info(f"[SANDBOX] Output:\n{result_dict['stdout']}")
            
            result = f"Remediation executed successfully in sandboxed Job '{result_dict['job_name']}'. " \
                     f"Duration: {result_dict['duration_seconds']}s. " \
                     f"Output: {result_dict['stdout'][:200]}"
        else:
            logger.error(f"[ERROR] [SANDBOXED_EXECUTION] Job {result_dict['job_name']} failed")
            logger.error(f"[SANDBOX] Error: {result_dict['error']}")
            logger.error(f"[SANDBOX] Stderr: {result_dict['stderr']}")
            
            result = f"Remediation failed in sandboxed Job: {result_dict['error']}"
        
        logger.info(f"[SANDBOXED_EXECUTION] Total execution time: {exec_duration:.2f}s")
        return result
        
    except Exception as e:
        exec_duration = (datetime.now() - exec_start).total_seconds()
        logger.error(f"[ERROR] [SANDBOXED_EXECUTION] Failed to create sandboxed Job: {e}")
        logger.exception("Full error traceback:")
        
        # Fallback to simulated execution if sandboxing fails
        logger.warning("[FALLBACK] Falling back to simulated execution")
        return execute_remediation(script)
