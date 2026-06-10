# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.11
FROM python:${PYTHON_VERSION}-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY src ./src

ENV MCP_PORT=4010
ENV BACKEND_API_URL=http://backend:8000

EXPOSE 4010

CMD ["python", "src/main.py"]
