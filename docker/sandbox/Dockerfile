ARG BASE_IMAGE=python:3.12-slim-bookworm
FROM ${BASE_IMAGE}
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-writer fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir PyMuPDF==1.26.7 python-docx==1.2.0 pdf2docx==0.5.8
RUN useradd --uid 1000 --create-home sandbox
COPY worker.py /app/worker.py
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
USER 1000:1000
WORKDIR /outputs
ENTRYPOINT ["python", "-I", "/app/worker.py"]
