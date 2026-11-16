# Technical Presentation Outline (≈10 slides, 8-10 min)

**1. Title** - Enterprise AI Knowledge Assistant (name, course, date)

**2. Problem & goal** - Employees waste time searching PDFs/policies. Goal: upload PDFs, ask natural-language questions, get cited answers. Delivered end-to-end: RAG app → Docker → Kubernetes → CI/CD → monitoring & logging.

**3. Architecture** (draw the diagram from README)
Browser → FastAPI → ingestion pipeline → vector index on PVC → LLM → answer + sources. Side channel: /metrics → Prometheus → Grafana; stdout JSON → Promtail → Loki → Grafana.

**4. RAG pipeline**
- Ingest: pypdf extract per page → 900-char chunks, 150 overlap → index (page + filename metadata)
- Query: vectorise question → cosine top-4 → prompt with context → LLM ("answer only from context, cite [n]")
- Fallback: extractive answer if no API key / LLM fails → app never hard-fails
- Why RAG: no retraining, answers grounded in your docs, citations reduce hallucination

**5. Design decisions** - FastAPI (async, auto docs); TF-IDF default for offline demo, FastEmbed switchable; OpenAI-compatible client = provider-agnostic; JSON logs for easy querying in Loki; non-root container.

**6. Containerisation** - Dockerfile (slim image, non-root, HEALTHCHECK); docker-compose for the full local stack.

**7. Kubernetes deployment** - Namespace, ConfigMap, Secret (API key), PVC (persist index), Deployment (probes, resource limits), Service (NodePort), ServiceMonitor. Explain: 1 replica + Recreate because RWO volume; scaling path = external vector DB.

**8. CI/CD** - GitHub Actions: lint+test → build → push GHCR → kind-cluster smoke test → deploy (kubeconfig secret). Show a green run.

**9. Monitoring & logging (show Grafana live)**
Dashboard panels: request rate, p95 latency, 5xx rate, docs/chunks, questions by mode, retrieval vs LLM latency, CPU/memory, live logs, error logs, log volume by level. Prometheus targets page + Loki query.

**10. Limitations & future work + Q&A** - auth, multi-replica with Qdrant/pgvector, OCR, reranking, evaluation of answer quality, alerting rules (Alertmanager).

# Likely viva questions (short answers)
- *What is RAG?* Retrieve relevant chunks first, then give them to the LLM as context so it answers from your data.
- *Why chunk with overlap?* Keeps sentences that straddle a boundary retrievable.
- *Why not just send the whole PDF to the LLM?* Context limits, cost, latency, noise.
- *How do you handle hallucination?* Strict prompt, cited sources, "I don't know" when retrieval finds nothing.
- *Why Recreate strategy / 1 replica?* The index is on a ReadWriteOnce PVC; two pods can't mount it.
- *Liveness vs readiness?* Liveness restarts a dead container; readiness gates traffic.
- *How do metrics get to Grafana?* App exposes /metrics → ServiceMonitor tells Prometheus Operator to scrape → Grafana queries Prometheus.
- *How do logs get to Grafana?* App logs JSON to stdout → Promtail DaemonSet tails node log files → Loki → Grafana Explore/logs panel.
