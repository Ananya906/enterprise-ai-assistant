"""RAG engine: PDF -> chunks -> vector index -> retrieval -> answer generation.

Embedding backends (EMBEDDING_BACKEND):
  tfidf     (default) scikit-learn TF-IDF vectors; no downloads, works offline.
  fastembed neural embeddings (pip install fastembed); better semantic recall.

Answer generation:
  If LLM_API_KEY is set, calls any OpenAI-compatible /chat/completions endpoint
  (OpenAI, Groq, Gemini-compat, Ollama, ...). Otherwise (or on failure) falls
  back to an extractive answer built from the best-matching sentences.
"""
import io
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
import numpy as np
from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from . import metrics

log = logging.getLogger("rag")

CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", "900"))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", "150"))
TOP_K = int(os.getenv("TOP_K", "4"))


@dataclass
class Chunk:
    id: str
    doc_id: str
    filename: str
    page: int
    text: str


# ---------------------------------------------------------------- chunking
def split_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP):
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):  # try to break on a sentence / space boundary
            cut = max(text.rfind(". ", start, end), text.rfind(" ", start + size // 2, end))
            if cut > start:
                end = cut + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


# ---------------------------------------------------------------- vector stores
class TfidfStore:
    name = "tfidf"

    def __init__(self):
        self.texts: list[str] = []
        self.vec = None
        self.matrix = None

    def rebuild(self, texts):
        self.texts = list(texts)
        if not self.texts:
            self.vec = self.matrix = None
            return
        self.vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
        self.matrix = self.vec.fit_transform(self.texts)

    def scores(self, query: str):
        if self.vec is None:
            return np.zeros(0)
        return cosine_similarity(self.vec.transform([query]), self.matrix).ravel()


class FastEmbedStore:
    name = "fastembed"

    def __init__(self):
        from fastembed import TextEmbedding  # optional dependency

        self.model = TextEmbedding(os.getenv("EMBED_MODEL", "BAAI/bge-small-en-v1.5"))
        self.matrix = np.zeros((0, 1))

    def rebuild(self, texts):
        texts = list(texts)
        if not texts:
            self.matrix = np.zeros((0, 1))
            return
        m = np.array(list(self.model.embed(texts)))
        self.matrix = m / np.linalg.norm(m, axis=1, keepdims=True)

    def scores(self, query: str):
        if not len(self.matrix):
            return np.zeros(0)
        q = np.array(list(self.model.embed([query])))[0]
        return self.matrix @ (q / np.linalg.norm(q))


def make_store():
    backend = os.getenv("EMBEDDING_BACKEND", "tfidf").lower()
    if backend == "fastembed":
        try:
            return FastEmbedStore()
        except Exception:  # noqa: BLE001
            log.exception("fastembed unavailable, falling back to tfidf")
    return TfidfStore()


# ---------------------------------------------------------------- engine
class RagEngine:
    def __init__(self, data_dir: str):
        self.dir = Path(data_dir)
        (self.dir / "pdfs").mkdir(parents=True, exist_ok=True)
        self.index_file = self.dir / "index.json"
        self.lock = threading.Lock()
        self.docs: dict[str, dict] = {}
        self.chunks: list[Chunk] = []
        self.store = make_store()
        self._load()

    # persistence ---------------------------------------------------
    def _load(self):
        if self.index_file.exists():
            raw = json.loads(self.index_file.read_text())
            self.docs = raw["docs"]
            self.chunks = [Chunk(**c) for c in raw["chunks"]]
            self.store.rebuild([c.text for c in self.chunks])
            log.info("index loaded", extra={"documents": len(self.docs), "chunks": len(self.chunks)})
        self._update_gauges()

    def _save(self):
        tmp = self.index_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"docs": self.docs, "chunks": [asdict(c) for c in self.chunks]}))
        tmp.replace(self.index_file)

    def _update_gauges(self):
        metrics.DOCS_GAUGE.set(len(self.docs))
        metrics.CHUNKS_GAUGE.set(len(self.chunks))

    # ingestion -----------------------------------------------------
    def ingest_pdf(self, filename: str, data: bytes) -> dict:
        reader = PdfReader(io.BytesIO(data))
        doc_id = uuid.uuid4().hex[:12]
        new_chunks = []
        for page_no, page in enumerate(reader.pages, start=1):
            for piece in split_text(page.extract_text() or ""):
                new_chunks.append(Chunk(uuid.uuid4().hex[:12], doc_id, filename, page_no, piece))
        if not new_chunks:
            raise ValueError("No extractable text found (scanned/image-only PDF?)")
        with self.lock:
            (self.dir / "pdfs" / f"{doc_id}.pdf").write_bytes(data)
            self.chunks.extend(new_chunks)
            doc = {"id": doc_id, "filename": filename, "pages": len(reader.pages),
                   "chunks": len(new_chunks), "uploaded_at": time.time()}
            self.docs[doc_id] = doc
            self.store.rebuild([c.text for c in self.chunks])
            self._save()
            self._update_gauges()
        log.info("document ingested", extra={"doc_id": doc_id, "file": filename,
                                             "pages": doc["pages"], "chunks": doc["chunks"]})
        return doc

    def delete(self, doc_id: str) -> bool:
        with self.lock:
            if doc_id not in self.docs:
                return False
            del self.docs[doc_id]
            self.chunks = [c for c in self.chunks if c.doc_id != doc_id]
            (self.dir / "pdfs" / f"{doc_id}.pdf").unlink(missing_ok=True)
            self.store.rebuild([c.text for c in self.chunks])
            self._save()
            self._update_gauges()
        log.info("document deleted", extra={"doc_id": doc_id})
        return True

    # retrieval -----------------------------------------------------
    def search(self, question: str, doc_id: str | None = None, k: int = TOP_K):
        with metrics.RETRIEVAL_LATENCY.time():
            scores = self.store.scores(question)
        order = np.argsort(scores)[::-1]
        hits = []
        for i in order:
            c = self.chunks[i]
            if doc_id and c.doc_id != doc_id:
                continue
            if scores[i] <= 0.01:
                break
            hits.append((c, float(scores[i])))
            if len(hits) == k:
                break
        return hits

    # answering -----------------------------------------------------
    def ask(self, question: str, doc_id: str | None = None) -> dict:
        hits = self.search(question, doc_id)
        sources = [{"filename": c.filename, "page": c.page, "score": round(s, 3),
                    "snippet": c.text[:300]} for c, s in hits]
        if not hits:
            metrics.QUESTIONS.labels(mode="no_context").inc()
            return {"answer": "I couldn't find anything relevant in the uploaded documents.",
                    "mode": "no_context", "sources": []}

        contexts = [c for c, _ in hits]
        mode, answer = "extractive", None
        if os.getenv("LLM_API_KEY"):
            try:
                answer = call_llm(question, contexts)
                mode = "llm"
            except Exception:  # noqa: BLE001
                metrics.LLM_ERRORS.inc()
                log.exception("LLM call failed, using extractive fallback")
        if answer is None:
            answer = extractive_answer(question, contexts)
        metrics.QUESTIONS.labels(mode=mode).inc()
        return {"answer": answer, "mode": mode, "sources": sources}


# ---------------------------------------------------------------- generation
def call_llm(question: str, contexts: list[Chunk]) -> str:
    context = "\n\n".join(f"[{i}] ({c.filename}, p.{c.page}) {c.text}" for i, c in enumerate(contexts, 1))
    body = {
        "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": (
                "You are an enterprise knowledge assistant. Answer ONLY from the provided context. "
                "Cite sources like [1], [2]. If the answer is not in the context, say you don't know.")},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
        ],
    }
    url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
    with metrics.LLM_LATENCY.time():
        r = httpx.post(url, json=body, timeout=60,
                       headers={"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


_WORD = re.compile(r"[a-z0-9]+")


def extractive_answer(question: str, contexts: list[Chunk], n: int = 3) -> str:
    q_terms = set(_WORD.findall(question.lower())) - {"the", "a", "an", "is", "of", "to", "what", "how", "and", "in"}
    scored = []
    for ci, c in enumerate(contexts):
        for s in re.split(r"(?<=[.!?])\s+", c.text):
            terms = set(_WORD.findall(s.lower()))
            if len(s) > 25:
                scored.append((len(q_terms & terms) / (1 + 0.02 * len(terms)), ci, s.strip()))
    scored.sort(reverse=True)
    best = [s for _, _, s in scored[:n]] or [contexts[0].text[:300]]
    return " ".join(best) + "\n\n(Extractive answer - set LLM_API_KEY for generated answers.)"
