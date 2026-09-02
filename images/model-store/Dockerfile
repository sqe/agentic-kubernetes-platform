FROM python:3.12-slim
RUN groupadd --gid 65532 runtime && useradd --uid 65532 --gid runtime --create-home runtime
WORKDIR /app
COPY images/model-store/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY images/model-store/model_store.py .
USER 65532:65532
ENTRYPOINT ["python", "/app/model_store.py"]
