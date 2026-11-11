"""OpenTelemetry setup.

Spans follow the OpenTelemetry GenAI semantic conventions where they apply. Model spans
carry the logical alias, never a real deployment name, and no document text.
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_configured = False


def setup_telemetry(service_name: str, otlp_endpoint: str | None) -> None:
    """Install a tracer provider once. Without an endpoint, spans are created but not exported."""
    global _configured
    if _configured:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True))
        )
    trace.set_tracer_provider(provider)
    _configured = True


def tracer() -> trace.Tracer:
    return trace.get_tracer("axiom")


def shutdown_telemetry() -> None:
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.force_flush()
