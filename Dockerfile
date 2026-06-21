FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY fakturace/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fakturace/ .
COPY timetrack/pdf_export.py /timetrack/pdf_export.py
RUN mkdir -p /app/data
EXPOSE 8732
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8732"]
