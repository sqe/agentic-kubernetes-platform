import asyncio
import json
from typing import Any
from uuid import uuid4

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from platform_runtime.contracts import JsonRpcRequest
from platform_runtime.settings import settings


class VisionAgentClient:
    """Correlated Kafka RPC client for the independently scalable vision agent."""

    def __init__(self) -> None:
        self.enabled = bool(settings.kafka_bootstrap_servers and settings.vision_model)
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self.producer: AIOKafkaProducer | None = None
        self.consumer: AIOKafkaConsumer | None = None
        self.collector: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        if not self.enabled:
            return
        common = {
            "bootstrap_servers": settings.kafka_bootstrap_servers,
            "security_protocol": settings.kafka_security_protocol,
        }
        self.producer = AIOKafkaProducer(
            acks="all",
            enable_idempotence=True,
            value_serializer=lambda value: json.dumps(value).encode(),
            **common,
        )
        self.consumer = AIOKafkaConsumer(
            "results.vision",
            group_id=f"knowledge-vision-{uuid4()}",
            auto_offset_reset="latest",
            value_deserializer=lambda raw: json.loads(raw.decode()),
            **common,
        )
        await self.producer.start()
        await self.consumer.start()
        self.collector = asyncio.create_task(self._collect())

    async def close(self) -> None:
        if self.collector:
            self.collector.cancel()
            try:
                await self.collector
            except asyncio.CancelledError:
                pass
        if self.consumer:
            await self.consumer.stop()
        if self.producer:
            await self.producer.stop()

    async def describe(self, image_base64: str, page: int) -> str:
        if not self.producer:
            raise RuntimeError("Vision agent client is not started")
        request = JsonRpcRequest(
            method="vision.describe",
            params={
                "image_base64": image_base64,
                "media_type": "image/jpeg",
                "prompt": (
                    f"Analyze the extracted picture from PDF page {page}. Identify its named "
                    "objects and describe the image, diagram, or chart; transcribe relevant "
                    "labels, legends, axes, and measurements, but ignore surrounding page text. "
                    "State relationships and measurements useful for a knowledge graph."
                ),
            },
        )
        future = asyncio.get_running_loop().create_future()
        self.pending[request.id] = future
        await self.producer.send_and_wait("tasks.vision", request.model_dump(mode="json"))
        try:
            result = await asyncio.wait_for(future, settings.vision_timeout_seconds)
        finally:
            self.pending.pop(request.id, None)
        if error := result.get("error"):
            raise RuntimeError(error.get("message", "Vision agent failed"))
        return str(result["result"]["caption"])

    async def _collect(self) -> None:
        assert self.consumer
        async for message in self.consumer:
            task_id = str(message.value.get("id", ""))
            if future := self.pending.get(task_id):
                if not future.done():
                    future.set_result(message.value)
