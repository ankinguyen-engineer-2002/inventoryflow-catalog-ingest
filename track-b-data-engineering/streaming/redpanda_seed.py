#!/usr/bin/env python3
"""Seed the Redpanda `inventory.changes` topic with sample events.

Used to exercise the RisingWave streaming SQL pipeline end-to-end. Each
event published here propagates through the materialized view within
approximately one second.

Usage:
    poetry run python streaming/redpanda_seed.py [--count N]
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime

from confluent_kafka import Producer


BOOTSTRAP = "localhost:19092"
TOPIC = "inventory.changes"
DEALER_ID = "7207c961-a7cc-46a7-9c5e-34b292a2cc68"

SAMPLE_PARTS = [
    "602006-0015",
    "313001-0008",
    "602001-0014",
    "602017-0003",
    "313003-0011",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=10)
    ap.add_argument("--delay", type=float, default=0.2)
    args = ap.parse_args()

    producer = Producer({"bootstrap.servers": BOOTSTRAP, "linger.ms": 5})

    for i in range(args.count):
        part = SAMPLE_PARTS[i % len(SAMPLE_PARTS)]
        event = {
            "event_id": str(uuid.uuid4()),
            "dealer_id": DEALER_ID,
            "part_number": part,
            "stock_level": 100 - i,
            "timestamp": datetime.utcnow().isoformat(timespec="seconds"),
        }
        producer.produce(TOPIC, value=json.dumps(event).encode("utf-8"))
        producer.poll(0)
        print(f"  → {event['part_number']} stock={event['stock_level']}")
        time.sleep(args.delay)

    producer.flush(timeout=10)
    print(f"\n✓ Published {args.count} events to {TOPIC}")


if __name__ == "__main__":
    main()
