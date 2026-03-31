"""
tracing.py — OpenTelemetry setup for auto-infra-remediation.

Exports traces via OTLP HTTP to Jaeger (or any OTel collector).
All graph nodes create child spans so the full alert → LangGraph → execution
path is visible as a single distributed trace.

Configure via env vars:
  OTEL_EXPORTER_OTLP_ENDPOINT  (default: http://localhost:4318)
  OTEL_SERVICE_NAME             (default: auto-infra-remediation)
  OTEL_ENABLED                  (default: true — set false to disable)
"""

import os
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource

_ENABLED = os.getenv("OTEL_ENABLED", "true").lower() != "false"
_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
_SERVICE = os.getenv("OTEL_SERVICE_NAME", "auto-infra-remediation")


def setup_tracing():
    """Initialise TracerProvider. Call once at startup before importing graph."""
    if not _ENABLED:
        # Install a no-op provider so tracer calls are safe but silent
        trace.set_tracer_provider(TracerProvider())
        return

    resource = Resource.create({"service.name": _SERVICE})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{_ENDPOINT}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(_SERVICE)
