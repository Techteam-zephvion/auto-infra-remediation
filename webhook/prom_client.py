import os
import requests
from logger import get_logger

logger = get_logger("prom_client")

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")


def get_prometheus_metrics(alertname: str, pod_name: str) -> str:
    """
    Fetch relevant metrics from Prometheus based on the alert type and pod.
    """
    logger.info(f"[PROM] Fetching metrics | alertname={alertname} | pod={pod_name} | url={PROMETHEUS_URL}")

    queries = {
        "HighCPUUsage": f'rate(container_cpu_usage_seconds_total{{namespace="default", pod="{pod_name}"}}[1m])',
        "HighCPULoad": f'rate(process_cpu_seconds_total{{job="failure-simulator", pod="{pod_name}"}}[5m])',
        "HighMemoryUsage": f'container_memory_working_set_bytes{{namespace="default", pod="{pod_name}"}}',
        "MemoryLeakDetected": f'process_resident_memory_bytes{{job="failure-simulator", pod="{pod_name}"}}',
        "HighErrorRate": f'rate(http_requests_total{{status="500", pod="{pod_name}"}}[5m])'
    }

    query = queries.get(alertname)
    if not query:
        logger.warning(f"[PROM] No PromQL query mapped for alertname='{alertname}'. Skipping metrics fetch.")
        return f"No specific PromQL query mapped for alert: {alertname}"

    logger.debug(f"[PROM] Executing query: {query}")

    try:
        response = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": query},
            timeout=5
        )
        response.raise_for_status()
        data = response.json()
        logger.debug(f"[PROM] HTTP {response.status_code} received from Prometheus")

        results = data.get("data", {}).get("result", [])
        if not results:
            logger.warning(f"[PROM] Query returned 0 results. query='{query}'")
            return f"Query '{query}' returned no data."

        logger.info(f"[PROM] Query returned {len(results)} metric series.")
        summary = f"PromQL Query: {query}\n"
        for result in results:
            value = result.get("value", [])
            if len(value) == 2:
                timestamp, val = value
                summary += f"- Metric Value: {val}\n"

        return summary

    except requests.exceptions.Timeout:
        logger.error(f"[PROM] ❌ Request timed out after 5s. Is Prometheus reachable at {PROMETHEUS_URL}?")
        return f"Error: Prometheus request timed out at {PROMETHEUS_URL}"
    except requests.exceptions.ConnectionError:
        logger.error(f"[PROM] ❌ Connection refused to Prometheus at {PROMETHEUS_URL}. Is port-forward active?")
        return f"Error: Cannot connect to Prometheus at {PROMETHEUS_URL}"
    except Exception as e:
        logger.error(f"[PROM] ❌ Unexpected error fetching metrics: {e}", exc_info=True)
        return f"Error fetching metrics from Prometheus: {str(e)}"
