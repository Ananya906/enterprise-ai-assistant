import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from . import metrics
from .logging_conf import setup_logging
from .rag import RagEngine

setup_logging(os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("api")

MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))
engine = RagEngine(os.getenv("DATA_DIR", "./data"))
app = FastAPI(title="Enterprise AI Knowledge Assistant", version="1.0.0")


@app.middleware("http")
async def observe(request: Request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        elapsed = time.perf_counter() - start
        route = request.scope.get("route")
        path = route.path if route else "unmatched"
        if path != "/metrics":
            metrics.HTTP_REQUESTS.labels(request.method, path, str(status)).inc()
            metrics.HTTP_LATENCY.labels(request.method, path).observe(elapsed)
            log.info("request", extra={"method": request.method, "path": request.url.path,
                                       "status": status, "duration_ms": round(elapsed * 1000, 1)})


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    doc_id: str | None = None


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def ui():
    return (Path(__file__).parent / "static" / "index.html").read_text()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    return {"status": "ready", "documents": len(engine.docs), "chunks": len(engine.chunks)}


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/upload")
def upload(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        metrics.UPLOAD_ERRORS.inc()
        raise HTTPException(400, "Only PDF files are supported")
    data = file.file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        metrics.UPLOAD_ERRORS.inc()
        raise HTTPException(413, f"File larger than {MAX_UPLOAD_MB} MB")
    try:
        doc = engine.ingest_pdf(file.filename, data)
    except ValueError as e:
        metrics.UPLOAD_ERRORS.inc()
        raise HTTPException(422, str(e))
    except Exception:  # noqa: BLE001
        metrics.UPLOAD_ERRORS.inc()
        log.exception("PDF ingestion failed", extra={"file": file.filename})
        raise HTTPException(400, "Could not read this PDF (corrupt or encrypted?)")
    metrics.DOCS_UPLOADED.inc()
    return doc


@app.get("/documents")
def documents():
    return sorted(engine.docs.values(), key=lambda d: d["uploaded_at"], reverse=True)


@app.delete("/documents/{doc_id}")
def delete_document(doc_id: str):
    if not engine.delete(doc_id):
        raise HTTPException(404, "Document not found")
    return {"deleted": doc_id}


@app.post("/ask")
def ask(q: Question):
    if not engine.docs:
        raise HTTPException(409, "Upload a PDF first")
    if q.doc_id and q.doc_id not in engine.docs:
        raise HTTPException(404, "Document not found")
    result = engine.ask(q.question, q.doc_id)
    log.info("question answered", extra={"mode": result["mode"], "sources": len(result["sources"]),
                                         "question_len": len(q.question)})
    return result
