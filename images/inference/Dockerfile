# vLLM includes CUDA kernels, an OpenAI-compatible API, tensor parallelism, and prefix caching.
ARG VLLM_VERSION=v0.10.1.1
FROM vllm/vllm-openai:${VLLM_VERSION}
ENV HF_HOME=/models/.huggingface \
    VLLM_WORKER_MULTIPROC_METHOD=spawn
ENTRYPOINT ["python3", "-m", "vllm.entrypoints.openai.api_server"]
CMD ["--model", "/models/model", "--host", "0.0.0.0", "--port", "8000", "--enable-prefix-caching"]
