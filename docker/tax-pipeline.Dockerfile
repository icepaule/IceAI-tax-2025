FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       pst-utils \
       procps \
       tesseract-ocr \
       poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
