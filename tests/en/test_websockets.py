"""Test websocket API."""
import asyncio
import json
import logging
import os
import queue
import sys
import threading
import unittest
from concurrent.futures import CancelledError
from uuid import uuid4

import paho.mqtt.client as mqtt
import requests
import websockets
from rhasspyhermes.asr import AsrTextCaptured
from rhasspyhermes.intent import Intent, Slot
from rhasspyhermes.nlu import NluIntent
from rhasspyhermes.wake import HotwordDetected

_LOGGER = logging.getLogger(__name__)


class WebsocketEnglishTests(unittest.TestCase):
    """Test websockets (English)"""

    def setUp(self):
        self.loop = asyncio.get_event_loop()
        self.http_port = os.environ.get("RHASSPY_HTTP_PORT", 12101)
        self.mqtt_port = int(os.environ.get("RHASSPY_MQTT_PORT") or 1883)
        self.client = mqtt.Client()

        connected_event = threading.Event()
        self.client.on_connect = lambda *args: connected_event.set()

        self.client.connect("localhost", self.mqtt_port)
        self.client.loop_start()

        # Block until connected
        connected_event.wait(timeout=5)

        self.siteId = "default"
        self.sessionId = str(uuid4())

    def tearDown(self):
        self.client.loop_stop()

    def api_url(self, fragment):
        return f"http://localhost:{self.http_port}/api/{fragment}"

    def ws_url(self, fragment):
        return f"ws://localhost:{self.http_port}/api/{fragment}"

    def check_status(self, response):
        if response.status_code != 200:
            print(response.text, file=sys.stderr)

        response.raise_for_status()

    # -------------------------------------------------------------------------

    async def async_ws_receive(self, url_fragment, event_queue, connected_event):
        try:
            url = self.ws_url(url_fragment)
            _LOGGER.debug(url)

            async with websockets.connect(url) as websocket:
                connected_event.set()

                while True:
                    data = await websocket.recv()
                    await event_queue.put(data)
        except CancelledError:
            pass

    # -------------------------------------------------------------------------

    def test_ws_text(self):
        """Calls async_test_ws_text"""
        self.loop.run_until_complete(self.async_test_ws_text())

    async def async_test_ws_text(self):
        """Test api/events/text endpoint"""
        # Start listening
        event_queue = asyncio.Queue()
        connected = asyncio.Event()
        receive_task = asyncio.ensure_future(
            self.async_ws_receive("events/text", event_queue, connected)
        )
        await asyncio.wait_for(connected.wait(), timeout=5)

        # Send in a message
        text_captured = AsrTextCaptured(
            text="this is a test",
            likelihood=1,
            seconds=0,
            siteId=self.siteId,
            sessionId=self.sessionId,
            wakewordId=str(uuid4()),
        )

        self.client.publish(text_captured.topic(), text_captured.payload())

        # Wait for response
        event = json.loads(await asyncio.wait_for(event_queue.get(), timeout=5))
        self.assertEqual(text_captured.text, event.get("text", ""))
        self.assertEqual(text_captured.siteId, event.get("siteId", ""))
        self.assertEqual(text_captured.wakewordId, event.get("wakewordId", ""))

        # Stop listening
        receive_task.cancel()

    # -------------------------------------------------------------------------

    def test_ws_intent(self):
        """Calls async_test_ws_intent"""
        self.loop.run_until_complete(self.async_test_ws_intent())

    async def async_test_ws_intent(self):
        """Test api/events/intent endpoint"""
        # Start listening
        event_queue = asyncio.Queue()
        connected = asyncio.Event()
        receive_task = asyncio.ensure_future(
            self.async_ws_receive("events/intent", event_queue, connected)
        )
        await asyncio.wait_for(connected.wait(), timeout=5)

        # Send in a message
        nlu_intent = NluIntent(
            input="turn on the living room lamp",
            id=str(uuid4()),
            intent=Intent(intentName="ChangeLightState", confidenceScore=1),
            slots=[
                Slot(
                    entity="state",
                    slotName="state",
                    value="on",
                    confidence=1,
                    raw_value="on",
                ),
                Slot(
                    entity="name",
                    slotName="name",
                    value="living room lamp",
                    confidence=1,
                    raw_value="living room lamp",
                ),
            ],
            siteId=self.siteId,
            sessionId=self.sessionId,
        )

        self.client.publish(
            nlu_intent.topic(intentName=nlu_intent.intent.intentName),
            nlu_intent.payload(),
        )

        # Wait for response
        event = json.loads(await asyncio.wait_for(event_queue.get(), timeout=5))

        # Expected Rhasspy JSON format as a response
        expected = nlu_intent.to_rhasspy_dict()

        self.assertEqual(event, expected)

        # Stop listening
        receive_task.cancel()

    # -------------------------------------------------------------------------

    def test_ws_wake(self):
        """Calls async_test_ws_wake"""
        self.loop.run_until_complete(self.async_test_ws_wake())

    async def async_test_ws_wake(self):
        """Test api/events/wake endpoint"""
        # Start listening
        event_queue = asyncio.Queue()
        connected = asyncio.Event()
        receive_task = asyncio.ensure_future(
            self.async_ws_receive("events/wake", event_queue, connected)
        )
        await asyncio.wait_for(connected.wait(), timeout=5)

        # Send in a message
        detected = HotwordDetected(modelId=str(uuid4()), siteId=self.siteId)
        wakewordId = str(uuid4())

        self.client.publish(detected.topic(wakewordId=wakewordId), detected.payload())

        # Wait for response
        event = json.loads(await asyncio.wait_for(event_queue.get(), timeout=5))
        self.assertEqual(wakewordId, event.get("wakewordId", ""))
        self.assertEqual(detected.siteId, event.get("siteId", ""))

        # Stop listening
        receive_task.cancel()

    # -------------------------------------------------------------------------

    def test_ws_mqtt(self):
        """Calls async_test_ws_mqtt"""
        self.loop.run_until_complete(self.async_test_ws_mqtt())

    async def async_test_ws_mqtt(self):
        """Test api/mqtt websocket endpoint"""
        # Start listening
        topic = str(uuid4())
        payload = {"id": str(uuid4())}

        event_queue = asyncio.Queue()
        connected = asyncio.Event()
        receive_task = asyncio.create_task(
            self.async_ws_receive(f"mqtt/{topic}", event_queue, connected)
        )
        await asyncio.wait_for(connected.wait(), timeout=5)

        # Send in a message
        self.client.publish(topic, json.dumps(payload))

        # Wait for response
        event = json.loads(await asyncio.wait_for(event_queue.get(), timeout=5))

        # Check topic/payload
        self.assertEqual(topic, event.get("topic", ""))
        self.assertEqual(payload, event.get("payload", {}))

        # Stop listening
        receive_task.cancel()
