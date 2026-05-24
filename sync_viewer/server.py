#!/usr/bin/env python3
"""OTIO Sync protocol debug viewer — FastAPI + WebSocket backend.

Usage:
    python server.py [--host HOST] [--port PORT]
                     [--rmq-host HOST] [--rmq-port PORT]
                     [--session SESSION_ID]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

import opentimelineio as otio
from otio_sync_core import SyncManager, RabbitMQNetwork
from otio_sync_core.manager import STATE_DISCOVERING

# ── Global mutable state ───────────────────────────────────────────────────────

app = FastAPI()
_clients: set[WebSocket] = set()

_manager: SyncManager | None = None
_config: dict[str, Any] = {}

_DISCOVERY_TIMEOUT = 2.0
_REDISCOVERY_INTERVAL = 5.0
_JOINING_TIMEOUT = 10.0

# ── Serialisation helpers ──────────────────────────────────────────────────────

def _serialize_timeline(tl: otio.schema.Timeline, guid: str) -> dict:
    # First pass: build clip_guid → timeline start (s) for all non-annotation items.
    # Annotation clips use this to compute their absolute position from
    # source_range.start_time (clip-local frame) + the owning clip's timeline start.
    clip_timeline_start: dict[str, float] = {}
    for track in tl.tracks:
        if track.name and track.name.startswith("Annotations"):
            continue
        t = 0.0
        for item in track:
            item_guid = item.metadata.get("sync", {}).get("guid", "")
            if item_guid:
                clip_timeline_start[item_guid] = t
            t += item.duration().to_seconds()

    tracks = []
    for track in tl.tracks:
        items = []
        t = 0.0
        is_ann = bool(track.name and track.name.startswith("Annotations"))
        for item in track:
            dur = item.duration().to_seconds()
            item_guid = item.metadata.get("sync", {}).get("guid", "")
            start = t
            if is_ann and item.source_range is not None:
                ann_clip_guid = item.metadata.get("clip_guid", "")
                media_t = clip_timeline_start.get(ann_clip_guid)
                if media_t is not None:
                    start = media_t + item.source_range.start_time.to_seconds()
            items.append({
                "guid": item_guid,
                "name": item.name or "",
                "type": type(item).__name__,
                "start": round(start, 6),
                "duration": round(dur, 6),
            })
            t += dur
        tracks.append({
            "name": track.name or "",
            "kind": getattr(track, "kind", ""),
            "guid": track.metadata.get("sync", {}).get("guid", ""),
            "duration": round(t, 6),
            "items": items,
        })
    return {
        "guid": guid,
        "name": tl.name or "(untitled)",
        "tracks": tracks,
    }


def _detail_for_guid(guid: str) -> dict | None:
    if _manager is None:
        return None
    obj = _manager.object_map.get(guid)
    if obj is None:
        return None
    try:
        full = json.loads(otio.adapters.write_to_string(obj, "otio_json"))
    except Exception as e:
        full = {"error": str(e)}
    return {
        "guid": guid,
        "name": getattr(obj, "name", ""),
        "type": type(obj).__name__,
        "otio": full,
    }


def _build_state() -> dict:
    if _manager is None:
        return {"status": "not_started", "timelines": [], "playback": {}}
    timelines = [
        _serialize_timeline(tl, guid)
        for guid, tl in _manager.timelines.items()
    ]
    return {
        "status": _manager.status,
        "is_master": _manager.is_master,
        "self_guid": _manager.self_guid,
        "master_guid": _manager.master_guid,
        "active_timeline_guid": _manager.active_timeline_guid,
        "timelines": timelines,
        "playback": _manager.playback_state,
        "display": _manager.display_state,
    }


# ── WebSocket helpers ──────────────────────────────────────────────────────────

async def _push_all(msg: str) -> None:
    dead: set[WebSocket] = set()
    for ws in list(_clients):
        try:
            await ws.send_text(msg)
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)


async def _push_state() -> None:
    await _push_all(json.dumps({"type": "state", "data": _build_state()}))


# ── Background poll loop ───────────────────────────────────────────────────────

async def _poll_loop() -> None:
    discovery_deadline = time.monotonic() + _DISCOVERY_TIMEOUT
    joining_start: float | None = None

    while True:
        try:
            # tick() auto-handles the join handshake; returns only app-level events.
            app_events = _manager.tick()
            changed = bool(app_events)
        except Exception as e:
            print(f"[viewer] poll error: {e}", flush=True)
            await asyncio.sleep(1.0)
            continue

        now = time.monotonic()

        # Re-broadcast discovery while waiting for a master
        if _manager.status == STATE_DISCOVERING and now > discovery_deadline:
            _manager.broadcast_master_discovery()
            discovery_deadline = now + _REDISCOVERY_INTERVAL
            changed = True

        # Track JOINING entry time and re-request state if stuck
        if _manager.status == "JOINING":
            if joining_start is None:
                joining_start = now
            elif now - joining_start > _JOINING_TIMEOUT and _manager.master_guid:
                print("[viewer] JOINING timed out — re-requesting state", flush=True)
                _manager.request_state()
                joining_start = now
        else:
            joining_start = None

        if changed:
            await _push_state()

        await asyncio.sleep(0.1)


# ── FastAPI routes ─────────────────────────────────────────────────────────────

@app.on_event("startup")
async def _startup() -> None:
    global _manager
    cfg = _config
    network = RabbitMQNetwork(
        host=cfg.get("rmq_host", "localhost"),
        port=cfg.get("rmq_port", 5672),
        session_id=cfg.get("session", "otio-sync-demo"),
    )
    _manager = SyncManager(
        session_id=cfg.get("session", "otio-sync-demo"),
        network=network,
    )
    _manager.start_session()
    print(
        f"[viewer] Session '{cfg.get('session', 'otio-sync-demo')}' — "
        f"peer {_manager.self_guid[:8]}",
        flush=True,
    )
    asyncio.create_task(_poll_loop())


@app.get("/")
async def _index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/state")
async def _api_state() -> JSONResponse:
    return JSONResponse(_build_state())


@app.get("/api/debug")
async def _api_debug() -> JSONResponse:
    """Dump raw timeline structure — useful for diagnosing missing clips."""
    if _manager is None:
        return JSONResponse({"error": "no manager"})
    result: dict = {"status": _manager.status, "timelines": {}}
    for guid, tl in _manager.timelines.items():
        tl_info: dict = {"name": tl.name, "tracks": []}
        try:
            for track in tl.tracks:
                items_info = []
                for item in track:
                    try:
                        dur = item.duration().to_seconds()
                    except Exception as e:
                        dur = f"ERROR:{e}"
                    items_info.append({
                        "type": type(item).__name__,
                        "name": item.name,
                        "duration_s": dur,
                        "source_range": str(getattr(item, "source_range", None)),
                        "media_ref": str(getattr(item, "media_reference", None)),
                    })
                tl_info["tracks"].append({
                    "name": track.name,
                    "kind": getattr(track, "kind", ""),
                    "item_count": len(items_info),
                    "items": items_info,
                })
        except Exception as e:
            tl_info["error"] = str(e)
        result["timelines"][guid] = tl_info
    return JSONResponse(result)


@app.get("/api/detail/{guid}")
async def _api_detail(guid: str) -> JSONResponse:
    d = _detail_for_guid(guid)
    if d is None:
        raise HTTPException(status_code=404, detail="object not found")
    return JSONResponse(d)


@app.websocket("/ws")
async def _ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _clients.add(ws)
    await ws.send_text(json.dumps({"type": "state", "data": _build_state()}))
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                if msg.get("type") == "select":
                    detail = _detail_for_guid(msg.get("guid", ""))
                    await ws.send_text(json.dumps({"type": "detail", "data": detail}))
            except Exception:
                pass
    except WebSocketDisconnect:
        _clients.discard(ws)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(description="OTIO Sync debug viewer")
    p.add_argument("--host", default="localhost", help="Web server host (default: localhost)")
    p.add_argument("--port", type=int, default=8765, help="Web server port (default: 8765)")
    p.add_argument("--rmq-host", default="localhost", help="RabbitMQ host (default: localhost)")
    p.add_argument("--rmq-port", type=int, default=5672, help="RabbitMQ port (default: 5672)")
    p.add_argument("--session", default="otio-sync-demo", help="Session ID (default: otio-sync-demo)")
    args = p.parse_args()

    _config.update({
        "rmq_host": args.rmq_host,
        "rmq_port": args.rmq_port,
        "session": args.session,
    })

    print(f"[viewer] Starting at http://{args.host}:{args.port}", flush=True)
    print(f"[viewer] RabbitMQ: {args.rmq_host}:{args.rmq_port}", flush=True)
    print(f"[viewer] Session:  {args.session}", flush=True)

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
