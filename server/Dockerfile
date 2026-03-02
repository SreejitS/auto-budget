FROM python:3.11-slim

WORKDIR /app

COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY shared/ ./shared/
COPY server/src/ ./server/src/
COPY config/ ./config/

ENV PYTHONPATH=/app

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "30", "server.src.api:app"]
