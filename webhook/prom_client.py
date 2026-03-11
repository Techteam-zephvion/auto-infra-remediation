import os
import requests

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")

def get_prometheus_metrics(alertname: str, pod_name: str) -> str:
    """
    Fetch relevant metrics from Prometheus based on the alert type and pod.
    """
    queries = {
        "HighCPULoad": f'rate(process_cpu_seconds_total{{job="failure-simulator", pod="{pod_name}"}}[5m])',
        "MemoryLeakDetected": f'process_resident_memory_bytes{{job="failure-simulator", pod="{pod_name}"}}',
        "HighErrorRate": f'rate(http_requests_total{{status="500", pod="{pod_name}"}}[5m])'
    }
    
    query = queries.get(alertname)
    if not query:
        return f"No specific PromQL query mapped for alert: {alertname}"
        
    try:
        response = requests.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": query}, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        results = data.get("data", {}).get("result", [])
        if not results:
            return f"Query '{query}' returned no data."
            
        summary = f"PromQL Query: {query}\n"
        for result in results:
            value = result.get("value", [])
            if len(value) == 2:
                timestamp, val = value
                summary += f"- Metric Value: {val}\n"
        
        return summary
    except Exception as e:
        return f"Error fetching metrics from Prometheus: {str(e)}"
