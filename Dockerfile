FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/

# Set Python path so modules resolve correctly
ENV PYTHONPATH=/app/src

# Run the master orchestrator
CMD ["python", "src/main.py"]
