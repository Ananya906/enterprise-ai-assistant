import io
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ.pop("LLM_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.main import app  # noqa: E402
from app.rag import split_text  # noqa: E402

client = TestClient(app)


def make_pdf(lines):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    y = 800
    for line in lines:
        c.drawString(50, y, line)
        y -= 20
    c.save()
    return buf.getvalue()


PDF = make_pdf([
    "Employee Leave Policy.",
    "Full-time employees receive 24 days of paid annual leave per year.",
    "Sick leave requires a medical certificate after three consecutive days.",
    "Remote work is allowed up to two days per week with manager approval.",
])


def test_health_and_ready():
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").status_code == 200


def test_ask_before_upload_is_rejected():
    assert client.post("/ask", json={"question": "anything here"}).status_code == 409


def test_rejects_non_pdf():
    r = client.post("/upload", files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_upload_ask_delete_flow():
    r = client.post("/upload", files={"file": ("policy.pdf", PDF, "application/pdf")})
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["chunks"] >= 1

    assert len(client.get("/documents").json()) == 1

    r = client.post("/ask", json={"question": "How many days of annual leave do employees get?"})
    assert r.status_code == 200
    body = r.json()
    assert "24 days" in body["answer"]
    assert body["sources"][0]["filename"] == "policy.pdf"

    metrics = client.get("/metrics").text
    assert "kb_documents_uploaded_total" in metrics
    assert "http_requests_total" in metrics

    assert client.delete(f"/documents/{doc['id']}").status_code == 200
    assert client.delete(f"/documents/{doc['id']}").status_code == 404


def test_split_text_overlap_and_size():
    chunks = split_text("word " * 1000, size=200, overlap=50)
    assert len(chunks) > 5
    assert all(len(c) <= 200 for c in chunks)
