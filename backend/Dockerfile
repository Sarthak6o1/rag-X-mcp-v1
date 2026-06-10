# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# CPU-only PyTorch first — avoids multi-GB CUDA wheels (default pip torch on Linux).
# Matches typical docker-compose usage (no GPU). Smaller image; GitHub Actions CI won't run out of disk.
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
