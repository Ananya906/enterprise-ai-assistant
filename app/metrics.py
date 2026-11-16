"""Prometheus metrics exposed on /metrics."""
from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "http_requests_total", "HTTP requests", ["method", "path", "status"]
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds", "HTTP request latency", ["method", "path"]
)
DOCS_UPLOADED = Counter("kb_documents_uploaded_total", "PDFs successfully ingested")
UPLOAD_ERRORS = Counter("kb_upload_errors_total", "Failed PDF uploads")
QUESTIONS = Counter("kb_questions_total", "Questions answered", ["mode"])
RETRIEVAL_LATENCY = Histogram("kb_retrieval_duration_seconds", "Vector search latency")
LLM_LATENCY = Histogram("kb_llm_duration_seconds", "LLM call latency")
LLM_ERRORS = Counter("kb_llm_errors_total", "LLM call failures (fell back to extractive)")
DOCS_GAUGE = Gauge("kb_documents", "Documents currently indexed")
CHUNKS_GAUGE = Gauge("kb_chunks", "Chunks currently indexed")
