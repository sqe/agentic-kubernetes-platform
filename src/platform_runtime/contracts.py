from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, HttpUrl


class Skill(BaseModel):
    id: str
    description: str


class AgentCard(BaseModel):
    name: str
    description: str
    endpoint: HttpUrl
    task_topic: str
    result_topic: str
    skills: list[Skill]
    version: str = "1.0.0"


class JsonRpcRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str = Field(default_factory=lambda: str(uuid4()))
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    skill: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class JsonRpcError(BaseModel):
    code: int
    message: str
    data: dict[str, Any] | None = None


class JsonRpcResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: str
    result: dict[str, Any] | None = None
    error: JsonRpcError | None = None


def normalize_task_request(payload: dict[str, Any]) -> JsonRpcRequest:
    """Accept native JSON-RPC or the Model Fleet tasks.execute envelope."""
    request = JsonRpcRequest.model_validate(payload)
    if request.method != "tasks.execute":
        return request
    skill = request.params.get("skill")
    if not isinstance(skill, str) or not skill:
        return request
    context = request.params.get("context") or {}
    if not isinstance(context, dict):
        context = {}
    params = dict(context)
    if prompt := request.params.get("prompt"):
        params.setdefault("prompt", prompt)
    if user_id := request.params.get("user_id"):
        params.setdefault("user_id", user_id)
    return request.model_copy(update={"method": skill, "params": params})


class Registration(BaseModel):
    card: AgentCard
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
