# Bring your own model or agent

## Bring your own model (BYOM)

```mermaid
flowchart LR
    Request[Prompt and parameters] --> Supervisor
    Supervisor --> Registry[(Live skill registry)]
    Supervisor --> Gateway[OpenAI-compatible BYOM endpoint]
    Gateway --> Choice[Strict JSON skill choice]
    Choice --> Allowlist{Registered skill?}
    Allowlist -->|No| Reject[Reject invented route]
    Allowlist -->|Yes| Kafka[(Kafka task topic)]
    Kafka --> Agent[Selected specialist agent]
```

The native supervisor supports any fixed OpenAI-compatible chat endpoint. Set
`LLM_GATEWAY_URL`, `LLM_GATEWAY_MODEL`, and optionally
`LLM_GATEWAY_API_KEY` in `platform-runtime-secrets`. `POST /v1/route` accepts a
prompt, asks the configured model to choose one skill from the active registry,
strictly validates the JSON response, rejects invented skills, and only then
publishes JSON-RPC to Kafka.

An explicit `skill` bypasses model selection but still must resolve through the
registry. The gateway is therefore a planner, not a policy or authorization
boundary. Keep it private, constrain egress to its fixed endpoint, and protect
the public supervisor route with the deployment's API/OIDC boundary.

```json
{
  "prompt": "Show the weather in Berlin",
  "params": {"units": "metric"}
}
```

The same vLLM endpoint can serve application inference and supervisor routing.
Model Fleet's Qwen examples show GPU-fit declarations and the important limits
of a 27B model on one 24 GiB card. A hosted provider also works when it exposes
the same API contract and its key comes from a Secret.

For local Apple Silicon development, Qwen3.8 text inference runs at
`host.docker.internal:8081` and the separate Qwen3-VL vision backend runs at
`:8082`; Metal stays on the macOS host while agents remain in Kind. See the
[complete MLX bridge setup](../README.md#apple-silicon-mlx-local-gpu-bridge).
MLX remains a loopback-only development backend—not a Kubernetes workload or
production API server.

The current self-hosted example is
[`qwen38-27b-gateway.yaml`](../deploy/model-fleet/qwen38-27b-gateway.yaml):
Qwen3.8-27B AWQ-INT4 on vLLM 0.17.0, selected by the
`values-model-fleet.yaml` profile. Its checkpoint is about 21.02 GB. On a 24
GiB GPU the example therefore uses a 4K context, two sequences, FP8 KV cache,
eager execution, and 6 GiB host-memory offload. This is a constrained
experiment—not a plentiful fit. Prefer a 32/48 GiB GPU for production or a
smaller official AWQ model when 24 GiB headroom matters. Hydrate the pinned
revision from S3/RustFS for repeatable startup rather than repeatedly fetching
mutable upstream files.

Knowledge extraction and embeddings are configured separately with
`OPENAI_BASE_URL`/`OPENAI_MODEL` and
`EMBEDDING_BASE_URL`/`EMBEDDING_MODEL`. Separate clients prevent Qdrant or one
model provider's credentials from being sent to another endpoint.

## Bring your own agent (BYOA)

```mermaid
flowchart TB
    Code[Dedicated agent directory] --> Card[Versioned Agent Card]
    Card --> Registry[(Registry)]
    Code --> Consumer[JSON-RPC Kafka consumer]
    Consumer --> Result[Correlated result producer]
    Code --> Ops[Health, metrics, and discovery]
    Consumer --> Helm[Helm workload and NetworkPolicy]
    Helm --> KEDA[KEDA lag scaler]
    Card --> Fleet[Optional Model Fleet registration]
```

An agent needs no supervisor code change. It must:

1. own a directory under `agents/` and a dedicated `tasks.<agent>` and
   `results.<agent>` topic;
2. publish an Agent Card with exact skill IDs and refresh its registration;
3. consume JSON-RPC tasks, publish a correlated result, then commit the Kafka
   input offset;
4. expose only discovery, health, and metrics over HTTP;
5. make handlers idempotent because delivery is at least once;
6. add a Helm workload, NetworkPolicy, and KEDA lag trigger.

PostgreSQL makes registrations durable across registry restarts. Model Fleet can
register the same card as an `AgentRegistration` and route it from Slack through
its authenticated supervisor. Tenant-bearing graph skills require an audited
Slack-to-OIDC identity mapping and are intentionally not enabled by default.

See [adding agents](ADDING_AGENTS.md), [agent internals](AGENT_ARCHITECTURE.md),
and [Model Fleet integration](MODEL_FLEET_INTEGRATION.md).
