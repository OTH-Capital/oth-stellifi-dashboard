FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir google-cloud-bigquery
COPY sync_to_bq.py ./
CMD ["python", "sync_to_bq.py"]
