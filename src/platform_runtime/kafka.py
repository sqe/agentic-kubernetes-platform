import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

logger = logging.getLogger(__name__)
Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class KafkaWorker:
    """At-least-once worker: publish a result before committing its input offset."""

    def __init__(
        self,
        bootstrap_servers: str,
        input_topic: str,
        output_topic: str,
        group_id: str,
        handler: Handler,
        security_protocol: str = "PLAINTEXT",
    ) -> None:
        common = {
            "bootstrap_servers": bootstrap_servers,
            "security_protocol": security_protocol,
        }
        self.consumer = AIOKafkaConsumer(
            input_topic,
            group_id=group_id,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            value_deserializer=lambda raw: json.loads(raw.decode()),
            **common,
        )
        self.producer = AIOKafkaProducer(
            acks="all",
            enable_idempotence=True,
            value_serializer=lambda value: json.dumps(value).encode(),
            **common,
        )
        self.output_topic = output_topic
        self.handler = handler

    async def run(self) -> None:
        await self.producer.start()
        await self.consumer.start()
        try:
            async for message in self.consumer:
                try:
                    result = await self.handler(message.value)
                except Exception as exc:
                    logger.exception("Task failed", extra={"topic": message.topic})
                    result = {
                        "jsonrpc": "2.0",
                        "id": str(message.value.get("id", "unknown")),
                        "error": {"code": -32000, "message": str(exc)},
                    }
                await self.producer.send_and_wait(self.output_topic, result)
                await self.consumer.commit()
        finally:
            await self.consumer.stop()
            await self.producer.stop()

    async def stop(self) -> None:
        await self.consumer.stop()
        await self.producer.stop()
