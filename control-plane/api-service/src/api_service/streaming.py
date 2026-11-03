"""Pump one deployment's pod logs to one WebSocket (LG10, LG12-LG14).

Each pod is read by its own daemon thread, because the Kubernetes client is synchronous (F3) and the shared
request threadpool must stay free for normal requests. Lines pass through a bounded queue; a client that
cannot keep up is closed with 1013 and never blocks anything else. Log text is never logged here (LG16).
"""
import asyncio
import json
import threading
import time
from typing import Callable

from .config import Settings
from .log_service import LogSession
from .ports import ClusterPort, KubeFailure

CLOSE_ENDED, CLOSE_SHUTDOWN, CLOSE_CLUSTER, CLOSE_SLOW, CLOSE_EXPIRED = 1000, 1001, 1011, 1013, 4001
REASONS = {CLOSE_ENDED: "stream ended", CLOSE_SHUTDOWN: "server shutting down", CLOSE_CLUSTER: "cluster failure",
           CLOSE_SLOW: "client too slow", CLOSE_EXPIRED: "token expired"}
POLL_SECONDS = 0.05  # how quickly shutdown, expiry and a disconnect are noticed


class _Control:
    overflow = False
    disconnected = False


async def run_stream(websocket, session: LogSession, cluster: ClusterPort, settings: Settings, state,
                     clock: Callable[[], float] = time.time) -> dict:
    """Send frames until the logs end, the token expires, the server stops or the client goes. Returns {code, lines}."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()  # held to settings.log_queue_max_lines by `slots`
    slots = threading.Semaphore(settings.log_queue_max_lines)
    ctl, stop, lock, handles = _Control(), threading.Event(), threading.Lock(), []

    def post(item) -> None:
        """Called from a reader thread. With no free slot the reader waits (backpressure, so a fast burst never
        closes a healthy client). Only a buffer that stays full for the send timeout means a stuck client (LG13)."""
        if not slots.acquire(timeout=settings.log_send_timeout_seconds):
            ctl.overflow = True
            stop.set()
            return
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:  # the loop is gone
            stop.set()

    def read(pod: str) -> None:
        try:
            stream = cluster.stream_pod_logs(session.namespace, pod, session.tail_lines, session.since_time)
            with lock:
                handles.append(stream)
            if stop.is_set():
                stream.close()
                return
            for line in stream:
                if stop.is_set():
                    break
                post(("line", pod, line))
        except KubeFailure as e:
            post(("error", pod, e.kind))
        except Exception as e:  # noqa: BLE001 - never leak internals; only the kind is reported
            post(("error", pod, type(e).__name__))
        finally:
            post(("end", pod, None))

    async def watch_disconnect() -> None:
        try:
            while True:
                if (await websocket.receive())["type"] == "websocket.disconnect":
                    break
        except Exception:  # noqa: BLE001 - any failure here means the client is gone
            pass
        ctl.disconnected = True

    for pod in session.pods:
        threading.Thread(target=read, args=(pod,), daemon=True, name=f"log-{pod}").start()
    watcher = asyncio.create_task(watch_disconnect())
    pending, lines, code = len(session.pods), 0, None
    try:
        while True:
            if ctl.disconnected:
                break
            if state.shutting_down:
                code = CLOSE_SHUTDOWN
            elif clock() >= session.stream_until:
                code = CLOSE_EXPIRED
            elif ctl.overflow:
                code = CLOSE_SLOW
            elif pending == 0 and queue.empty():
                code = CLOSE_ENDED
            if code is not None:
                break
            try:
                kind, pod, payload = await asyncio.wait_for(queue.get(), POLL_SECONDS)
            except asyncio.TimeoutError:
                continue
            slots.release()  # room for the reader again
            if kind == "end":
                pending -= 1
            elif kind == "error":
                code = CLOSE_CLUSTER
            else:
                frame = json.dumps({"pod": pod, "time": payload.time, "line": payload.line})
                try:
                    await asyncio.wait_for(websocket.send_text(frame), settings.log_send_timeout_seconds)
                except asyncio.TimeoutError:
                    code = CLOSE_SLOW
                except Exception:  # noqa: BLE001 - the connection is gone
                    ctl.disconnected = True
                else:
                    lines += 1
            if code is not None:
                break
    finally:
        stop.set()
        for _ in session.pods:  # frees a reader that is still waiting for room; it sees `stop` and ends
            slots.release()
        with lock:
            opened = list(handles)
        for stream in opened:  # closing unblocks any reader that is waiting for a line
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass
        watcher.cancel()
        try:
            await watcher
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    if code is not None and not ctl.disconnected:
        try:
            await websocket.close(code=code, reason=REASONS[code])
        except Exception:  # noqa: BLE001
            pass
    return {"code": None if ctl.disconnected else code, "lines": lines}
