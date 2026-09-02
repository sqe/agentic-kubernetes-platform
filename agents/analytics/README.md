# Analytics agent

The analytics specialist exposes `analytics.usage` and `analytics.errors` over
the platform's normal Kafka JSON-RPC path. It signs short-lived Cube Core API
tokens, executes fixed semantic queries, and returns the query with its rows so
every answer remains reproducible. Tenant filters originate from the
supervisor's authenticated conversation owner.

This is the self-hosted equivalent of the agent-to-agent pattern: the
supervisor delegates analytics to a specialist tool, and Cube owns governed BI.
Cube Cloud's streamed Chat API is a separate Premium/Enterprise capability and
is not claimed by this Cube Core example.
