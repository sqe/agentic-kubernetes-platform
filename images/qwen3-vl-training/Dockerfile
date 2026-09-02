ARG PYTORCH_VERSION=2.7.1-cuda12.8-cudnn9-devel
FROM pytorch/pytorch:${PYTORCH_VERSION}

ARG QWEN_VL_REF=96588727e44c78b25ba03ea03b8e12f7e64fd0da
WORKDIR /workspace
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/* && \
    git clone --filter=blob:none https://github.com/QwenLM/Qwen3-VL.git /opt/qwen3-vl && \
    git -C /opt/qwen3-vl checkout "$QWEN_VL_REF" && \
    rm -rf /opt/qwen3-vl/.git
COPY images/qwen3-vl-training/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    pip install --no-cache-dir --no-build-isolation flash-attn==2.7.4.post1
COPY images/qwen3-vl-training/train.py /workspace/train.py

ENV MODEL_PATH=Qwen/Qwen3-VL-4B-Instruct \
    ANNOTATION_PATH=/data/annotations.json \
    MEDIA_PATH=/data/images \
    OUTPUT_PATH=/output \
    HF_HOME=/models/huggingface
ENTRYPOINT ["python", "/workspace/train.py"]
