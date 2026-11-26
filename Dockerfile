# ReadMind Multi-Agent System — Render / Cloud Run / HF Spaces
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    EMBED_MODEL=hash \
    USE_RERANKER=false \
    LLM_PROVIDER=gemini \
    LLM_MODEL=gemini-2.0-flash \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# cache-bust: v3 - added networkx, tqdm, pydriller, langchain-core, llama-index-core
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

RUN mkdir -p data/index data/raw data/eval data/repos /tmp/readmind

EXPOSE 8080
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080}"]
