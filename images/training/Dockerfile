ARG PYTORCH_VERSION=2.7.1-cuda12.8-cudnn9-runtime
FROM pytorch/pytorch:${PYTORCH_VERSION}
WORKDIR /workspace
COPY images/training/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY images/training/train.py .
ENV MODEL_PATH=/models/base \
    DATASET_PATH=/data/train.jsonl \
    OUTPUT_PATH=/output
ENTRYPOINT ["python", "/workspace/train.py"]
