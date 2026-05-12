"""
Async Redis Stream Consumer for Latarnia

Registers as consumer group 'latarnia-dashboard' on every latarnia:streams:*
stream discovered in Redis, reads new messages, stores them in
latarnia:events:recent (for the REST initial-load endpoint), and broadcasts
each message to all connected WebSocket clients in real time.

Pub/sub is not used.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional, Set

import redis.asyncio as aioredis
from redis.exceptions import ResponseError
from fastapi import WebSocket


class WebSocketManager:
    """Tracks active dashboard WebSocket connections and broadcasts messages."""

    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    async def broadcast(self, data: str) -> None:
        dead: Set[WebSocket] = set()
        for ws in set(self._connections):
            try:
                await ws.send_text(data)
            except Exception:
                dead.add(ws)
        self._connections -= dead


class AsyncStreamConsumer:
    """Platform-level Redis Stream consumer.

    Reads from all latarnia:streams:* keys using consumer group
    'latarnia-dashboard'. Each message is stored in latarnia:events:recent
    and pushed to connected WebSocket clients.
    """

    _CONSUMER_GROUP = "latarnia-dashboard"
    _CONSUMER_NAME = "platform"
    _STREAM_PATTERN = b"latarnia:streams:*"
    _EVENTS_KEY = "latarnia:events:recent"

    def __init__(self, redis_url: str, max_events: int = 100) -> None:
        self._redis_url = redis_url
        self._max_events = max_events
        self.ws_manager = WebSocketManager()
        self._task: Optional[asyncio.Task] = None
        self._logger = logging.getLogger("latarnia.stream_consumer")

    async def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="AsyncStreamConsumer")
        self._logger.info("Stream consumer started")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._logger.info("Stream consumer stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        r: Optional[aioredis.Redis] = None
        try:
            while True:
                try:
                    if r is None:
                        r = aioredis.from_url(self._redis_url, decode_responses=False)
                        self._logger.info("Redis connection established for stream consumer")
                    await self._poll(r)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self._logger.error(f"Stream consumer error: {e}; retrying in 2s")
                    if r:
                        try:
                            await r.aclose()
                        except Exception:
                            pass
                        r = None
                    await asyncio.sleep(2)
        finally:
            if r:
                try:
                    await r.aclose()
                except Exception:
                    pass

    async def _poll(self, r: aioredis.Redis) -> None:
        """One poll cycle: discover streams, ensure groups, read messages."""
        stream_keys = await self._discover_streams(r)
        if not stream_keys:
            await asyncio.sleep(2)
            return

        # Ensure our consumer group exists on every discovered stream
        valid_keys = []
        for key in stream_keys:
            if await self._ensure_group(r, key):
                valid_keys.append(key)

        if not valid_keys:
            await asyncio.sleep(2)
            return

        # Block up to 2 s waiting for new messages across all streams
        streams = {key: ">" for key in valid_keys}
        results = await r.xreadgroup(
            self._CONSUMER_GROUP,
            self._CONSUMER_NAME,
            streams,
            count=50,
            block=2000,
        )
        if not results:
            return

        for raw_key, messages in results:
            stream_key = raw_key.decode("utf-8") if isinstance(raw_key, bytes) else raw_key
            for msg_id, fields in messages:
                await self._handle(r, stream_key, msg_id, fields)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _discover_streams(self, r: aioredis.Redis) -> list:
        """SCAN Redis for latarnia:streams:* keys of type STREAM."""
        keys = []
        cursor = 0
        while True:
            cursor, batch = await r.scan(
                cursor, match=self._STREAM_PATTERN, count=50, _type="STREAM"
            )
            for k in batch:
                keys.append(k.decode("utf-8") if isinstance(k, bytes) else k)
            if cursor == 0:
                break
        return keys

    async def _ensure_group(self, r: aioredis.Redis, key: str) -> bool:
        """Create the consumer group on the stream if it doesn't exist yet.

        Returns True if the group is ready, False if the key is not a stream.
        """
        try:
            await r.xgroup_create(key, self._CONSUMER_GROUP, id="$", mkstream=False)
            self._logger.info(f"Created consumer group '{self._CONSUMER_GROUP}' on {key}")
            return True
        except ResponseError as e:
            if "BUSYGROUP" in str(e):
                return True
            # WRONGTYPE or other error — key is not a valid stream
            self._logger.warning(f"Cannot create consumer group on {key}: {e}")
            return False

    async def _handle(
        self,
        r: aioredis.Redis,
        stream_key: str,
        msg_id: bytes,
        fields: dict,
    ) -> None:
        """Convert a stream entry, store it, broadcast it, then ACK."""
        try:
            decoded = {
                (k.decode("utf-8") if isinstance(k, bytes) else k):
                (v.decode("utf-8") if isinstance(v, bytes) else v)
                for k, v in fields.items()
            }

            source = decoded.get("source", stream_key.replace("latarnia:streams:", ""))
            msg_id_str = msg_id.decode("utf-8") if isinstance(msg_id, bytes) else str(msg_id)

            raw_ts = decoded.get("timestamp", "")
            try:
                ts_int = int(raw_ts) if raw_ts else int(msg_id_str.split("-")[0]) // 1000
            except (ValueError, IndexError):
                ts_int = 0

            timestamp_str = (
                datetime.fromtimestamp(ts_int).strftime("%Y-%m-%d %H:%M:%S")
                if ts_int else ""
            )

            raw_data = decoded.get("data", "{}")
            try:
                data_dict = json.loads(raw_data)
            except (json.JSONDecodeError, TypeError):
                data_dict = {"raw": raw_data}

            # message text: prefer data.content, then event_type, else full data
            if isinstance(data_dict, dict) and "content" in data_dict:
                message_text = data_dict["content"]
            elif "event_type" in decoded:
                message_text = f"Event: {decoded['event_type']}"
            else:
                message_text = json.dumps(data_dict)

            event_record = {
                "source": source,
                "timestamp": ts_int,
                "stream": stream_key,
                "stream_id": msg_id_str,
                "data": data_dict,
            }

            # Persist for the REST /api/activity/recent endpoint
            await r.rpush(self._EVENTS_KEY, json.dumps(event_record))
            await r.ltrim(self._EVENTS_KEY, -self._max_events, -1)

            # Broadcast to connected WebSocket clients
            ws_payload = json.dumps({
                "timestamp": timestamp_str,
                "message": message_text,
                "sender": source,
                "data": event_record,
            })
            await self.ws_manager.broadcast(ws_payload)

            self._logger.debug(f"Processed stream message from {stream_key} (id={msg_id_str})")

        except Exception as e:
            self._logger.error(f"Failed to handle message from {stream_key}: {e}")
        finally:
            # Always ACK to prevent PEL accumulation; this is a best-effort feed
            try:
                await r.xack(stream_key, self._CONSUMER_GROUP, msg_id)
            except Exception:
                pass
