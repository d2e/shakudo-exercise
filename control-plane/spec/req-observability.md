# Observability Requirements

Part of the spec set indexed in [`req.md`](./req.md). Requirements only.

OpenTelemetry (OTel) is the standard for logs and metrics. **No gateway or collector is deployed now**, but everything emitted must be ready to ship to one later with configuration only.

| # | Requirement |
| :-- | :--- |
| O1 | **Structured logs.** One JSON object per line on stdout. Fields follow the OTel log data model: `timestamp` (RFC 3339), `severity`, `body`, plus attributes. |
| O2 | **Log attributes** on request logs: `request_id`, `http.request.method`, `http.route`, `http.response.status_code`, `duration_ms`, `enduser.id` (JWT `sub`), `trace_id`, `span_id` (empty when no trace). |
| O3 | **No secrets in logs.** Never log tokens, `Authorization` headers, or `env` values from requests. |
| O4 | **OTLP export through the OTel SDK**, switched on only by the standard variables (`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_PROTOCOL`, `OTEL_SERVICE_NAME`, `OTEL_RESOURCE_ATTRIBUTES`). Unset means no export: logs stay on stdout and metrics are not exported. |
| O5 | **Export must never hurt the API.** Batched and asynchronous. If the collector is down or absent, data is dropped, requests are not slowed or failed, and at most one rate-limited warning is logged. |
| O6 | **Resource attributes:** `service.name=control-plane-api`, `service.version`, `deployment.environment`, `k8s.namespace.name`, `k8s.pod.name` (the last two from the Downward API). |
| O7 | **Metrics.** HTTP: `http.server.request.duration` (histogram) and `http.server.active_requests`, using OTel semantic-convention names. Application: a counter of deployment operations by `operation` and `outcome`, and a histogram of Kubernetes API call duration by `operation` and result class. |
| O8 | **Low cardinality.** Deployment names, UIDs, pod names and user ids are never metric attributes (logs only). Routes use the template (`/api/v1/deployments/{id}`), not the real path. |
| O9 | **Audit events.** Each create, patch, delete and restart logs one event with `event.name`, actor (`sub` and `iss`), namespace, name, UID and outcome. With the mock provider the actor is self-asserted (R26). |
| O10 | **Tracing readiness.** Accept and propagate the W3C `traceparent` header, and put `trace_id` and `span_id` in logs. Emitting traces is out of scope now. |
| O11 | **Configuration.** The OTel variables appear in `charts/app/values.yaml` and `values.schema.json` (per `AGENTS.md`), empty by default. `LOG_LEVEL` is configurable. |

## Assumptions to confirm

1. Python OTel SDK (`opentelemetry-sdk` and the OTLP exporter), matching the FastAPI stack.
2. Traces are out of scope for now (O10 only prepares for them).
3. When a collector is added later, its OTLP endpoint is reachable from the pod, and egress is opened for it then. The default-deny NetworkPolicy (R22) applies from the start, so that egress rule is added with the collector.
4. Metrics export interval is the SDK default (60 s).
5. Metrics are pushed over OTLP only. No Prometheus `/metrics` endpoint.
