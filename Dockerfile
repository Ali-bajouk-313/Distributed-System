FROM python:3.10-slim
WORKDIR /app
COPY requirements-runtime.txt .
RUN pip install --no-cache-dir -r requirements-runtime.txt
COPY storage ./storage
CMD ["uvicorn", "storage.node:app", "--host", "0.0.0.0", "--port", "8000"]
