FROM python:3.12-slim
WORKDIR /banco-atlantico/fraud-service
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    opentelemetry-bootstrap -a install
COPY main.py .
EXPOSE 8003
CMD ["opentelemetry-instrument", "python", "main.py"]
