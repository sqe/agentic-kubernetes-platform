# Weather agent

A stateless showcase agent that registers `weather.current` and
`weather.forecast`, consumes JSON-RPC tasks from `tasks.weather`, calls the
public Open-Meteo APIs, publishes to `results.weather`, and exports Prometheus
metrics. It does not require an LLM.

```json
{"jsonrpc":"2.0","id":"weather-1","method":"weather.current","params":{"location":"Reykjavik"}}
```

Send tasks through the supervisor; the agent has no HTTP task-processing route.
