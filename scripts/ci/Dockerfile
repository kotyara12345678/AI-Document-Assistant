# Minimal container for the deterministic GigaChat mock used by CI/E2E.
# Pure stdlib, no third-party packages.
FROM python:3.12-alpine

WORKDIR /app

COPY mock_gigachat.py /app/mock_gigachat.py

EXPOSE 18000

CMD ["python", "/app/mock_gigachat.py"]
