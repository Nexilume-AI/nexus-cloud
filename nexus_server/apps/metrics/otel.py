from __future__ import annotations

from django.conf import settings


def configure_otel() -> bool:
    if not getattr(settings, "NEXUS_OTEL_ENABLED", False):
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return False

    endpoint = getattr(settings, "NEXUS_OTEL_EXPORTER_OTLP_ENDPOINT", "")
    provider = TracerProvider(
        resource=Resource.create({"service.name": getattr(settings, "NEXUS_OTEL_SERVICE_NAME", "nexus-server")})
    )
    exporter = OTLPSpanExporter(endpoint=endpoint or None)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return True
