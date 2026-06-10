FROM python:3.12-slim
WORKDIR /banco-atlantico/api-gateway
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    opentelemetry-bootstrap -a install
COPY main.py .
EXPOSE 8000
CMD ["opentelemetry-instrument", "python", "main.py"]
