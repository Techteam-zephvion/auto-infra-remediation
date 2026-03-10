import os
from kubernetes import client, config

def init_k8s():
    """Initialize Kubernetes client."""
    try:
        # Tries to load in-cluster config first
        config.load_incluster_config()
    except config.ConfigException:
        # Fallbacks to kubeconfig
        config.load_kube_config()

def get_pod_logs(namespace: str, pod_name: str, tail_lines: int = 50) -> str:
    """Fetch logs from a specific pod."""
    init_k8s()
    v1 = client.CoreV1Api()
    try:
        logs = v1.read_namespaced_pod_log(name=pod_name, namespace=namespace, tail_lines=tail_lines)
        # API Optimization 3: Context Truncation (Max 2000 chars to save LLM tokens)
        if logs and len(logs) > 2000:
            logs = "..." + logs[-2000:]
        return logs
    except Exception as e:
        return f"Error fetching logs for {pod_name}: {str(e)}"

def get_pods_with_labels(namespace: str, label_selector: str) -> list:
    """Get pods matching a label selector."""
    init_k8s()
    v1 = client.CoreV1Api()
    try:
        pods = v1.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
        return [pod.metadata.name for pod in pods.items]
    except Exception as e:
        print(f"Error listing pods: {e}")
        return []

def execute_remediation(script: str) -> str:
    """
    Execute a validated remediation.
    In a real system, this could invoke an Ansible playbook or specific K8s API patches.
    For this demo, we'll log what would be done, and simulate a safe restart if requested.
    """
    print(f"\n[EXECUTION ENGINE] Running remediation:\n{script}\n")
    return "Remediation executed successfully."
