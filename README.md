# Enterprise AI Knowledge Assistant

Upload PDFs, ask questions, get answers grounded in your documents (RAG) with page-level source citations.
Containerised, deployed on Kubernetes, with centralised logging (Loki) and monitoring (Prometheus + Grafana).

```
 Browser ──► FastAPI (RAG) ──► PDF → chunks → vector index (PVC) ──► top-k context ──► LLM (or extractive fallback)
                │  /metrics ─────────► Prometheus ─┐
                │  JSON logs (stdout) ► Promtail ─► Loki ─┴──► Grafana dashboard
```

## 1. Features
- PDF upload / list / delete, per-page text extraction (pypdf), overlapping chunking
- Retrieval: TF-IDF cosine similarity by default (offline, zero downloads); neural embeddings optional (`EMBEDDING_BACKEND=fastembed`)
- Answers: any OpenAI-compatible LLM when `LLM_API_KEY` is set (OpenAI, Groq, Gemini-compat, Ollama...). Without a key, or if the LLM call fails, falls back to an extractive answer so the app always works
- Sources (file, page, score) returned with every answer; "I don't know" when nothing relevant is retrieved
- Web UI at `/`, REST API docs at `/docs`
- Observability: Prometheus metrics at `/metrics`, structured JSON logs, `/health` + `/ready` probes

## 2. API
| Method | Path | Purpose |
|---|---|---|
| POST | `/upload` | multipart PDF upload (max 20 MB) |
| GET | `/documents` | list indexed documents |
| DELETE | `/documents/{id}` | remove document |
| POST | `/ask` | `{"question": "...", "doc_id": null}` → answer + sources |
| GET | `/health`, `/ready`, `/metrics` | probes and Prometheus |

## 3. Run locally (no Docker)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
uvicorn app.main:app --reload       # http://localhost:8000
```
Optional LLM: `export LLM_API_KEY=... LLM_BASE_URL=https://api.openai.com/v1 LLM_MODEL=gpt-4o-mini`
(For Groq/Gemini/Ollama change `LLM_BASE_URL` and `LLM_MODEL` to what the provider documents.)

## 4. Docker
```bash
docker build -t enterprise-ai-assistant:latest .
docker run -p 8000:8000 -v kbdata:/data enterprise-ai-assistant:latest
```

## 5. Full local stack with monitoring (fastest way to demo dashboards)
```bash
docker compose up --build
```
- App http://localhost:8000 · Prometheus http://localhost:9090 · Grafana http://localhost:3000 (admin / admin)
- The dashboard **Enterprise AI Knowledge Assistant** is provisioned automatically (metrics + logs).

## 6. Kubernetes (minikube) 
```bash
minikube start --cpus 2 --memory 4096
eval $(minikube docker-env)                    # build straight into minikube's Docker
docker build -t enterprise-ai-assistant:latest .

# app
kubectl apply -f k8s/00-namespace.yaml -f k8s/10-config.yaml -f k8s/20-pvc.yaml \
              -f k8s/30-deployment.yaml -f k8s/40-service.yaml
kubectl -n kb-assistant rollout status deploy/kb-assistant
minikube service -n kb-assistant kb-assistant --url        # open the URL

# optional LLM key
kubectl -n kb-assistant create secret generic kb-assistant-secret --from-literal=LLM_API_KEY=sk-... \
        --dry-run=client -o yaml | kubectl apply -f - && kubectl -n kb-assistant rollout restart deploy/kb-assistant
```

### Monitoring + logging on Kubernetes
```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update
kubectl create ns monitoring

# Loki + Promtail (collects every pod's stdout, labels by pod label `app`)
helm install loki grafana/loki-stack -n monitoring --set promtail.enabled=true --set grafana.enabled=false

# Prometheus + Grafana (Loki datasource added via values file)
helm install monitoring prometheus-community/kube-prometheus-stack -n monitoring \
     -f monitoring/kube-prometheus-values.yaml

# scrape the app + load the dashboard
kubectl apply -f k8s/50-servicemonitor.yaml
kubectl -n monitoring create configmap kb-dashboard --from-file=monitoring/grafana/dashboards/kb-assistant.json
kubectl -n monitoring label configmap kb-dashboard grafana_dashboard=1

kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80    # admin / admin
```
Verify: Prometheus → Status → Targets shows `kb-assistant` UP; Grafana → Explore → Loki → `{app="enterprise-ai-assistant"}`.

## 7. CI/CD (GitHub Actions - `.github/workflows/ci-cd.yml`)
1. **test** - flake8 + pytest
2. **build** - Docker build; push to GHCR (`ghcr.io/<owner>/<repo>`) on `main`
3. **k8s-smoke-test** - spins up a throw-away `kind` cluster, loads the image, applies the manifests, waits for rollout, curls `/health`, `/ready`
4. **deploy** - on `main`, applies manifests to a real cluster if the repo secret `KUBE_CONFIG` (base64 kubeconfig) exists; skipped otherwise

## 8. Configuration
| Env var | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `./data` (`/data` in container) | index + PDFs |
| `EMBEDDING_BACKEND` | `tfidf` | `tfidf` or `fastembed` |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` / `TOP_K` | 900 / 150 / 4 | chunking & retrieval |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | empty / OpenAI / gpt-4o-mini | generation |
| `MAX_UPLOAD_MB` | 20 | upload limit |
| `LOG_LEVEL` | INFO | logging |

## 9. Metrics exposed
`http_requests_total`, `http_request_duration_seconds`, `kb_documents`, `kb_chunks`, `kb_documents_uploaded_total`,
`kb_upload_errors_total`, `kb_questions_total{mode}`, `kb_retrieval_duration_seconds`, `kb_llm_duration_seconds`, `kb_llm_errors_total`, plus default process CPU/memory.

## 10. Limitations / future work
- Index is stored on a ReadWriteOnce volume → 1 replica. To scale horizontally, move vectors to Qdrant/pgvector and PDFs to object storage (then add an HPA).
- No authentication / multi-tenant isolation (add OIDC + per-user collections for real enterprise use).
- Scanned/image-only PDFs are rejected (would need OCR).
- TF-IDF is keyword-based; use `fastembed` or a hosted embedding API for semantic matching.

## 11. Repo layout
```
app/            FastAPI app, RAG engine, metrics, logging, UI
tests/          pytest suite
Dockerfile, docker-compose.yml
k8s/            namespace, config/secret, PVC, deployment, service, ServiceMonitor
monitoring/     Prometheus/Promtail/Grafana provisioning, Helm values, dashboard JSON
.github/workflows/ci-cd.yml
docs/           presentation outline + demo script
```
