FROM python:3.12-slim
WORKDIR /banco-atlantico/transaction-service
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    opentelemetry-bootstrap -a install
COPY main.py .
EXPOSE 8002
CMD ["opentelemetry-instrument", "python", "main.py"]
