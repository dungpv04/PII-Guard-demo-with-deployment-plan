FROM python:3.12-slim

ARG INSTALL_MARKER=1
ARG TORCH_VERSION=2.14.1+cpu
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    TORCH_HOME=/models/torch \
    HF_HOME=/models/huggingface

RUN useradd --create-home --uid 10002 extractor
WORKDIR /app
COPY extractor/requirements.txt extractor/requirements-marker.txt /app/
RUN pip install --no-cache-dir -r requirements.txt && \
    if [ "$INSTALL_MARKER" = "1" ]; then \
      pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu "torch==${TORCH_VERSION}" && \
      pip install --no-cache-dir -r requirements-marker.txt; \
    fi
RUN if [ "$INSTALL_MARKER" = "1" ]; then \
      python -c "from marker.util import download_font; download_font()"; \
    fi
COPY extractor /app/extractor
USER 10002
EXPOSE 8000
CMD ["uvicorn", "extractor.service:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
