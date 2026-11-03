"""A fake log API: the ticket endpoint and the WebSocket on one address. Scripts decide what each connection does."""
import asyncio
import json
import threading
import time

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute

import canned


def frame(pod: str, stamp: str, line: str) -> dict:
    return {"pod": pod, "time": stamp, "line": line}


class FakeLogsApi:
    """scripts[n] is the list of actions for the n-th connection (the last script repeats):
    ("frame", dict) ("raw", text) ("close", code) ("sleep", seconds) ("call", fn) ("hold",) ("refuse",)"""

    def __init__(self):
        self.tickets, self.connections, self.refreshes, self.scripts = [], [], [], [[("close", 1000)]]
        self.ticket_replies, self.cookie = [], None
        self.next_access = "access-2"
        app = Starlette(routes=[
            Route("/api/v1/deployments/{uid}/logs/ticket", self._ticket, methods=["POST"]),
            Route("/api/v1/auth/refresh", self._refresh, methods=["POST"]),
            WebSocketRoute("/api/v1/logs/stream", self._stream)])
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error", ws="websockets"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        while not self.server.started:
            time.sleep(0.01)
        self.url = f"http://127.0.0.1:{self.server.servers[0].sockets[0].getsockname()[1]}"

    async def _ticket(self, request):
        self.tickets.append({"body": await request.json(), "headers": dict(request.headers), "uid": request.path_params["uid"]})
        if self.ticket_replies:
            status, body, headers = self.ticket_replies.pop(0) if len(self.ticket_replies) > 1 else self.ticket_replies[0]
            return JSONResponse(body, status_code=status, headers=headers)
        headers = {"Set-Cookie": self.cookie} if self.cookie else {}
        return JSONResponse({"ticket": f"tkt-{len(self.tickets)}", "streamPath": "/api/v1/logs/stream", "expiresIn": 30},
                            headers=headers)

    async def _refresh(self, request):
        self.refreshes.append(await request.json())
        return JSONResponse(canned.tokens(access=self.next_access, refresh="refresh-2"))

    async def _stream(self, ws):
        n = len(self.connections)
        self.connections.append({"path": ws.url.path, "query": ws.url.query, "headers": dict(ws.headers)})
        script = self.scripts[min(n, len(self.scripts) - 1)]
        if script and script[0] == ("refuse",):
            await ws.close()  # before accept: the handshake is refused
            return
        await ws.accept(subprotocol="logs.v1")
        for action in script:
            kind = action[0]
            if kind == "frame":
                await ws.send_text(json.dumps(action[1]))
            elif kind == "raw":
                await ws.send_text(action[1])
            elif kind == "sleep":
                await asyncio.sleep(action[1])
            elif kind == "call":
                action[1]()
            elif kind == "hold":
                await ws.receive()
            elif kind == "close":
                await ws.close(code=action[1])
                return

    def stop(self):
        self.server.should_exit = True
        self.thread.join(5)
