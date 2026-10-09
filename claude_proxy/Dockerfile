FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY claude_proxy/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY claude_proxy /app/claude_proxy
USER 10001
EXPOSE 4000
CMD ["uvicorn", "claude_proxy.app:app", "--host", "0.0.0.0", "--port", "4000", "--workers", "1", "--no-access-log"]
