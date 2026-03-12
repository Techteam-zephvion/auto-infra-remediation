import os
import logging
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
