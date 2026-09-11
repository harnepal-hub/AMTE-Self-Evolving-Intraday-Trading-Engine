"""CoinDCX public futures trade/order-book collector.

Research-only collector. It records raw public websocket events and does not
place orders. CoinDCX documents futures channels for order-book snapshots and
trades on the public websocket endpoint.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import Any

import socketio


SOCKET_ENDPOINT = "wss://stream.coindcx.com"


class CoinDCXMicrostructureCollector:
    def __init__(self, pair: str = "B-BTC_USDT", depth: int = 50, out_dir: str = "data/microstructure") -> None:
        if depth not in (10, 20, 50):
            raise ValueError("depth must be one of 10, 20, 50")
        self.pair = pair
        self.depth = depth
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.sio = socketio.Client(reconnection=True, logger=False, engineio_logger=False)
        self.files: dict[str, Any] = {}
        self.writers: dict[str, csv.DictWriter] = {}
        self.counts = {"trades": 0, "orderbook": 0}
        self._configure_handlers()

    def _open(self, kind: str, fields: list[str]) -> csv.DictWriter:
        if kind in self.writers:
            return self.writers[kind]
        path = self.out_dir / f"{self.pair.replace('/', '_')}_{kind}.csv"
        fh = path.open("a", newline="", encoding="utf-8")
        self.files[kind] = fh
        writer = csv.DictWriter(fh, fieldnames=fields)
        if fh.tell() == 0:
            writer.writeheader()
        self.writers[kind] = writer
        return writer

    def _configure_handlers(self) -> None:
        @self.sio.event
        def connect() -> None:
            self.sio.emit("join", {"channelName": f"{self.pair}@orderbook@{self.depth}-futures"})
            self.sio.emit("join", {"channelName": f"{self.pair}@trades-futures"})

        @self.sio.event
        def disconnect() -> None:
            print("CoinDCX websocket disconnected")

        @self.sio.on("depth-snapshot")
        def depth_snapshot(response: dict[str, Any]) -> None:
            data = response.get("data", response)
            ts = data.get("ts") or data.get("E") or int(time.time() * 1000)
            writer = self._open("orderbook", ["timestamp_ms", "version", "pair", "bids_json", "asks_json"])
            writer.writerow({
                "timestamp_ms": ts,
                "version": data.get("vs"),
                "pair": data.get("s", self.pair),
                "bids_json": json.dumps(data.get("bids", {}), separators=(",", ":")),
                "asks_json": json.dumps(data.get("asks", {}), separators=(",", ":")),
            })
            self.files["orderbook"].flush()
            self.counts["orderbook"] += 1

        @self.sio.on("new-trade")
        def new_trade(response: dict[str, Any]) -> None:
            data = response.get("data", response)
            writer = self._open("trades", ["timestamp_ms", "range_timestamp", "pair", "price", "quantity", "is_maker"])
            writer.writerow({
                "timestamp_ms": data.get("T"),
                "range_timestamp": data.get("RT"),
                "pair": data.get("s", self.pair),
                "price": data.get("p"),
                "quantity": data.get("q"),
                "is_maker": data.get("m"),
            })
            self.files["trades"].flush()
            self.counts["trades"] += 1

    def run(self) -> None:
        try:
            self.sio.connect(SOCKET_ENDPOINT, transports=["websocket"])
            print(f"Collecting {self.pair}: orderbook@{self.depth} + trades")
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("Stopping collector")
        finally:
            if self.sio.connected:
                self.sio.disconnect()
            for fh in self.files.values():
                fh.close()
            print(f"Saved events: {self.counts}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pair", default="B-BTC_USDT")
    parser.add_argument("--depth", type=int, default=50, choices=(10, 20, 50))
    parser.add_argument("--out", default="data/microstructure")
    args = parser.parse_args()
    CoinDCXMicrostructureCollector(args.pair, args.depth, args.out).run()


if __name__ == "__main__":
    main()
