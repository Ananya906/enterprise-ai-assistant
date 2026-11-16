# Demo script (5 min)

1. `kubectl get pods,svc,pvc -n kb-assistant` and `kubectl get pods -n monitoring` - everything Running.
2. Open the app URL (`minikube service -n kb-assistant kb-assistant --url`).
3. Upload a PDF (any policy/report with real text). Show "Indexed N chunks".
4. Ask 2-3 questions; point at the cited sources (file, page, score). Ask something not in the PDF → "couldn't find anything".
5. Show `/docs` (Swagger) and `/metrics` quickly.
6. Grafana → dashboard "Enterprise AI Knowledge Assistant": request rate and latency moved; documents/questions counters went up; logs panel shows the JSON log lines for your upload and questions.
7. Trigger an error on purpose (upload a .txt) → show it in the errors/warnings panel and `kb_upload_errors_total`.
8. Show the GitHub Actions run (green) and the repo structure.
9. `kubectl delete pod -n kb-assistant -l app=enterprise-ai-assistant` → pod restarts, documents still there (PVC persistence).
