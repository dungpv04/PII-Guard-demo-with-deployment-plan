FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY analyzer_plus/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY analyzer_plus /app/analyzer_plus
USER 10001
EXPOSE 3000
CMD ["uvicorn", "analyzer_plus.app:app", "--host", "0.0.0.0", "--port", "3000", "--workers", "1"]
