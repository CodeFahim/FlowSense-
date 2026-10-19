FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN useradd --create-home --uid 10001 lifeline
# Tesseract reads text from photographed or scanned documents (English language data only).
RUN apt-get update     && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng poppler-utils     && rm -rf /var/lib/apt/lists/*
COPY requirements.txt requirements-dev.txt ./
# Dev/test tools are included so the same image can run the test suite in Compose.
RUN pip install -r requirements-dev.txt
COPY . .
USER lifeline
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/api/health', timeout=2).status==200 else 1)"
# Migrations are idempotent; the demo seed runs only when no demo business exists (never resets data).
# PORT and WEB_CONCURRENCY let a host choose the port and worker count (defaults: 8000, 2).
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed --if-missing && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]
