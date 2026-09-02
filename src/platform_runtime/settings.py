from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    service: str = "weather"
    host: str = "0.0.0.0"
    port: int = 8100
    log_level: str = "INFO"
    kafka_bootstrap_servers: str | None = None
    kafka_security_protocol: str = "PLAINTEXT"
    registry_url: str = "http://registry:8001"
    postgres_url: str | None = None
    agent_endpoint: str = "http://weather:8100"
    knowledge_agent_endpoint: str = "http://knowledge-api:8200"
    registration_interval_seconds: int = 30
    registry_ttl_seconds: int = 90
    openai_base_url: str | None = None
    openai_api_key: str | None = None
    openai_model: str | None = None
    llm_gateway_url: str | None = None
    llm_gateway_model: str | None = None
    llm_gateway_api_key: str | None = None
    embedding_base_url: str | None = None
    embedding_model: str | None = None
    embedding_api_key: str | None = None
    mlflow_tracking_uri: str | None = None
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "change-me"
    jwt_jwks_url: str | None = None
    jwt_audience: str | None = None
    jwt_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_authorization_endpoint: str | None = None
    oidc_token_endpoint: str | None = None
    oidc_logout_endpoint: str | None = None
    oidc_registration_endpoint: str | None = None
    auth_disabled: bool = False
    document_source_hosts: str = ""
    object_store_endpoint: str | None = None
    object_store_bucket: str = "agent-documents"
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    qdrant_collection: str = "knowledge-chunks"
    embedding_chunk_chars: int = 6_000
    knowledge_chunk_chars: int = 24_000
    knowledge_max_chunks: int = 40
    redis_url: str | None = None
    cache_ttl_seconds: int = 300
    cube_url: str = "http://agentic-analytics-api:4000"
    cube_api_secret: str | None = None


settings = Settings()
