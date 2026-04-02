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

# Check if tracing is enabled before importing OpenTelemetry modules
_ENABLED = os.getenv("OTEL_ENABLED", "true").lower() != "false"

if _ENABLED:
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        OTEL_AVAILABLE = True
    except ImportError:
        OTEL_AVAILABLE = False
        # Create mock objects to prevent errors
        class MockTrace:
            def set_tracer_provider(self, provider): pass
            def get_tracer(self, name): return MockTracer()
        
        class MockTracer:
            def start_span(self, name, **kwargs):
                return MockSpan()
        
        class MockSpan:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def set_attribute(self, key, value): pass
            def set_status(self, status): pass
        
        trace = MockTrace()
else:
    OTEL_AVAILABLE = False
    # Create mock objects when tracing is disabled
    class MockTrace:
        def set_tracer_provider(self, provider): pass
        def get_tracer(self, name): return MockTracer()
    
    class MockTracer:
        def start_span(self, name, **kwargs):
            return MockSpan()
    
    class MockSpan:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def set_attribute(self, key, value): pass
        def set_status(self, status): pass
    
    trace = MockTrace()

_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
_SERVICE = os.getenv("OTEL_SERVICE_NAME", "auto-infra-remediation")


def setup_tracing():
    """Initialise TracerProvider. Call once at startup before importing graph."""
    if not _ENABLED or not OTEL_AVAILABLE:
        # Tracing disabled or OpenTelemetry not available - mock provider already set up
        return

    # Set up real OpenTelemetry tracing
    resource = Resource.create({"service.name": _SERVICE})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{_ENDPOINT}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


def get_tracer():
    return trace.get_tracer(_SERVICE)
