"""
Redis Event Subscriber for Latarnia

Subscribes to Redis pub/sub channels and polls Redis Streams, storing recent
events for dashboard display.
"""
import json
import logging
import threading
import time
from typing import Dict, Optional

import redis


class RedisEventSubscriber:
    """Background subscriber that listens to Redis pub/sub channels and streams"""

    def __init__(self, redis_url: str, max_events: int = 100):
        self.redis_url = redis_url
        self.max_events = max_events
        self.logger = logging.getLogger("latarnia.event_subscriber")

        self.redis_client: Optional[redis.Redis] = None
        self.pubsub: Optional[redis.client.PubSub] = None
        self.subscriber_thread: Optional[threading.Thread] = None
        self.stream_poll_thread: Optional[threading.Thread] = None
        self.shutdown_event = threading.Event()
        self.running = False
        # Tracks last-read stream entry ID per stream key (skip history on startup)
        self._stream_cursors: Dict[str, str] = {}
    
    def start(self):
        """Start the background subscriber thread"""
        if self.running:
            self.logger.warning("Event subscriber is already running")
            return
        
        try:
            # Connect to Redis
            self.redis_client = redis.from_url(self.redis_url, decode_responses=False)
            self.redis_client.ping()
            
            # Create pub/sub instance
            self.pubsub = self.redis_client.pubsub()
            
            # Subscribe to all latarnia event channels
            self.pubsub.psubscribe("latarnia:events:*")
            
            self.logger.info("Subscribed to latarnia:events:* channels")
            
            # Start pub/sub background thread
            self.subscriber_thread = threading.Thread(
                target=self._subscriber_loop,
                daemon=True,
                name="RedisEventSubscriber"
            )
            self.subscriber_thread.start()

            # Start Redis Streams polling thread
            self.stream_poll_thread = threading.Thread(
                target=self._stream_poll_loop,
                daemon=True,
                name="RedisStreamPoller"
            )
            self.stream_poll_thread.start()

            self.running = True
            self.logger.info("Redis event subscriber started (pub/sub + streams)")
            
        except Exception as e:
            self.logger.error(f"Failed to start event subscriber: {e}")
            self.running = False
    
    def stop(self):
        """Stop the background subscriber thread"""
        if not self.running:
            return
        
        self.logger.info("Stopping Redis event subscriber...")
        self.shutdown_event.set()
        
        if self.subscriber_thread and self.subscriber_thread.is_alive():
            self.subscriber_thread.join(timeout=5)

        if self.stream_poll_thread and self.stream_poll_thread.is_alive():
            self.stream_poll_thread.join(timeout=5)

        if self.pubsub:
            try:
                self.pubsub.close()
            except:
                pass
        
        if self.redis_client:
            try:
                self.redis_client.close()
            except:
                pass
        
        self.running = False
        self.logger.info("Redis event subscriber stopped")
    
    def _subscriber_loop(self):
        """Background loop that processes pub/sub messages"""
        self.logger.info("Event subscriber loop started")
        
        while not self.shutdown_event.is_set():
            try:
                # Get message with timeout
                message = self.pubsub.get_message(timeout=1.0)
                
                if message and message['type'] == 'pmessage':
                    # Process the event
                    self._process_event(message)
                
            except Exception as e:
                self.logger.error(f"Error in subscriber loop: {e}")
                time.sleep(1)
        
        self.logger.info("Event subscriber loop stopped")
    
    def _process_event(self, message):
        """Process a received pub/sub message and store it"""
        try:
            # Extract event data
            channel = message['channel'].decode('utf-8') if isinstance(message['channel'], bytes) else message['channel']
            data = message['data']
            
            if isinstance(data, bytes):
                data = data.decode('utf-8')
            
            # Parse JSON
            event_data = json.loads(data)
            
            # Store in recent events list
            events_key = "latarnia:events:recent"
            
            # Add to list
            self.redis_client.rpush(events_key, json.dumps(event_data))
            
            # Trim list to max size (keep only the most recent)
            self.redis_client.ltrim(events_key, -self.max_events, -1)
            
            self.logger.debug(f"Stored event from channel {channel}")
            
        except Exception as e:
            self.logger.error(f"Failed to process event: {e}")

    # ------------------------------------------------------------------
    # Redis Streams polling
    # ------------------------------------------------------------------

    def _stream_poll_loop(self):
        """Background loop that polls all latarnia:streams:* Redis Streams."""
        self.logger.info("Stream poll loop started")
        while not self.shutdown_event.is_set():
            try:
                self._poll_streams_once()
            except Exception as e:
                self.logger.error(f"Error in stream poll loop: {e}")
            self.shutdown_event.wait(timeout=2.0)
        self.logger.info("Stream poll loop stopped")

    def _discover_stream_keys(self) -> list:
        """Return all Redis Stream keys matching latarnia:streams:* via SCAN."""
        keys = []
        cursor = 0
        while True:
            cursor, batch = self.redis_client.scan(
                cursor, match=b"latarnia:streams:*", count=50, _type="STREAM"
            )
            for k in batch:
                keys.append(k.decode("utf-8") if isinstance(k, bytes) else k)
            if cursor == 0:
                break
        return keys

    def _init_stream_cursor(self, key: str) -> str:
        """Return the ID of the last entry in a stream so we skip history on startup."""
        try:
            result = self.redis_client.xrevrange(key, b"+", b"-", count=1)
            if result:
                entry_id = result[0][0]
                return entry_id.decode("utf-8") if isinstance(entry_id, bytes) else entry_id
        except Exception:
            pass
        return "0-0"

    def _poll_streams_once(self):
        """Discover streams and read any new messages from each."""
        stream_keys = self._discover_stream_keys()
        if not stream_keys:
            return

        # Initialise cursors for newly discovered streams (skip existing history)
        for key in stream_keys:
            if key not in self._stream_cursors:
                self._stream_cursors[key] = self._init_stream_cursor(key)

        streams = {k: self._stream_cursors[k] for k in stream_keys}
        results = self.redis_client.xread(streams, count=50)

        # Prune cursors for streams that no longer exist
        self._stream_cursors = {k: v for k, v in self._stream_cursors.items() if k in stream_keys}

        if not results:
            return

        for stream_key, messages in results:
            stream_key_str = stream_key.decode("utf-8") if isinstance(stream_key, bytes) else stream_key
            for msg_id, fields in messages:
                msg_id_str = msg_id.decode("utf-8") if isinstance(msg_id, bytes) else msg_id
                self._process_stream_message(stream_key_str, msg_id_str, fields)
                self._stream_cursors[stream_key_str] = msg_id_str

    def _process_stream_message(self, stream_key: str, msg_id: str, fields: dict):
        """Convert a Redis Stream entry to the latarnia:events:recent format and store it."""
        try:
            decoded = {}
            for k, v in fields.items():
                dk = k.decode("utf-8") if isinstance(k, bytes) else k
                dv = v.decode("utf-8") if isinstance(v, bytes) else v
                decoded[dk] = dv

            source = decoded.get("source", stream_key.replace("latarnia:streams:", ""))

            raw_ts = decoded.get("timestamp", "")
            try:
                timestamp = int(raw_ts) if raw_ts else int(msg_id.split("-")[0]) // 1000
            except (ValueError, IndexError):
                timestamp = 0

            raw_data = decoded.get("data", "{}")
            try:
                data_dict = json.loads(raw_data)
            except (json.JSONDecodeError, TypeError):
                data_dict = {"raw": raw_data}

            event_record = {
                "source": source,
                "timestamp": timestamp,
                "stream": stream_key,
                "stream_id": msg_id,
                "data": data_dict,
            }

            events_key = "latarnia:events:recent"
            self.redis_client.rpush(events_key, json.dumps(event_record))
            self.redis_client.ltrim(events_key, -self.max_events, -1)

            self.logger.debug(f"Stored stream event from {stream_key} (id={msg_id})")

        except Exception as e:
            self.logger.error(f"Failed to process stream message from {stream_key}: {e}")
