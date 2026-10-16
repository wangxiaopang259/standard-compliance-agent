FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=6002
EXPOSE 6002
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "6002"]
