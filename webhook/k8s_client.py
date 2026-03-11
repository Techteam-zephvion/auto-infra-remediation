import os
from kubernetes import client, config
from logger import get_logger

logger = get_logger("k8s_client")


def init_k8s():
    """Initialize Kubernetes client (in-cluster first, then kubeconfig fallback)."""
    try:
        config.load_incluster_config()
        logger.debug("K8s: Using in-cluster config.")
    except config.ConfigException:
        logger.debug("K8s: In-cluster config not found, falling back to kubeconfig.")
        config.load_kube_config()


def get_pod_logs(namespace: str, pod_name: str, tail_lines: int = 50) -> str:
    """Fetch logs from a specific pod, truncated to 2000 chars."""
    logger.info(f"[K8S] Fetching logs | pod={pod_name} | namespace={namespace} | tail_lines={tail_lines}")
    init_k8s()
    v1 = client.CoreV1Api()
    try:
        logs = v1.read_namespaced_pod_log(name=pod_name, namespace=namespace, tail_lines=tail_lines)
        original_len = len(logs)
        if logs and original_len > 2000:
            logs = "..." + logs[-2000:]
            logger.debug(f"[K8S] Logs truncated: {original_len} → 2000 chars (token limit)")
        else:
            logger.debug(f"[K8S] Logs fetched: {original_len} chars")
        return logs
    except Exception as e:
        logger.error(f"[K8S] ❌ Failed to fetch logs for pod '{pod_name}': {e}", exc_info=True)
        return f"Error fetching logs for {pod_name}: {str(e)}"


def get_pods_with_labels(namespace: str, label_selector: str) -> list:
    """Get pods matching a label selector."""
    logger.info(f"[K8S] Listing pods | namespace={namespace} | selector='{label_selector}'")
    init_k8s()
    v1 = client.CoreV1Api()
    try:
        pods = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
        names = [pod.metadata.name for pod in pods.items]
        logger.info(f"[K8S] Found {len(names)} pod(s): {names}")
        return names
    except Exception as e:
        logger.error(f"[K8S] ❌ Failed to list pods in namespace='{namespace}': {e}", exc_info=True)
        return []


def execute_remediation(script: str) -> str:
    """
    Execute a validated remediation.
    In a real system, this could invoke an Ansible playbook or specific K8s API patches.
    """
    logger.info(f"[EXECUTION] Running remediation script ({len(script)} chars):")
    for line in script.strip().splitlines():
        logger.info(f"  > {line}")
    return "Remediation executed successfully."
