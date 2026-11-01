"""Connecting a YouTube channel: the one-time consent, run by the server at the PC.

A Google account owns several channels; the consent screen asks which one, and the refresh token
is bound to it. So "connect a channel" is: open Google's page in a browser **on the server's
machine**, let the user pick the channel, catch the redirect on a loopback port here, trade the
code for the tokens, read the channel's handle, and write its token file
(``thm_core/youtube.py`` ``save_channel_token``). That is all it does: the channel joins the
server's list, and **no project points at it until the user picks it in the settings and saves**
(``set_project_settings youtube_channel``, from any client). The consent is started from a
project's settings only so its progress has a place to show.

The redirect must land on the machine the browser runs on, and Google allows plain http only on
loopback, so the start is refused ``pc_only`` unless the request came from this host
(``can_connect``). Progress goes out as the ``youtube_connect`` event and is readable at
``GET .../youtube/connect``; one consent at a time per project, ten minutes to finish it.
"""

from __future__ import annotations

import asyncio
import http.server
import secrets
import socket
import threading
import urllib.parse
from dataclasses import dataclass, field

from starlette.concurrency import run_in_threadpool

from thm_core import youtube
from thm_core.ids import utc_now

from .deps import ServerState
from .schemas import ChannelOut, ConnectStatusOut

CONNECT_EVENT = "youtube_connect"
CONSENT_TIMEOUT_S = 600.0
LOOPBACK = "127.0.0.1"


class _Catcher(http.server.HTTPServer):
    """A loopback listener for one redirect. ``handle_request`` is polled a second at a time so a
    cancel is noticed."""

    allow_reuse_address = True
    timeout = 1.0

    def __init__(self, state: str):
        super().__init__((LOOPBACK, 0), _Handler)
        self.state = state
        self.code: str | None = None
        self.error: str | None = None
        self.stop = threading.Event()

    def wait_for_code(self) -> str:
        """Runs in a thread; the thread owns the socket and closes it on its way out."""
        try:
            while self.code is None and self.error is None and not self.stop.is_set():
                self.handle_request()
        finally:
            try:
                self.server_close()
            except OSError:
                pass
        if self.code:
            return self.code
        raise youtube.YouTubeError(0, f"Google said: {self.error}" if self.error else "the connect was cancelled")


class _Handler(http.server.BaseHTTPRequestHandler):
    server: _Catcher

    def do_GET(self):  # noqa: N802 - the stdlib's name
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if q.get("state", [""])[0] != self.server.state:
            self._say(400, "This is not the consent THM is waiting for. Go back to THM and press Connect again.")
            return
        if "error" in q:
            self.server.error = q["error"][0]
            self._say(200, f"Google said: {self.server.error}. You can close this tab and go back to THM.")
        else:
            self.server.code = q.get("code", [None])[0]
            self._say(200, "THM has the channel. You can close this tab and go back to THM.")

    def _say(self, status: int, text: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(text.encode("utf-8"))

    def log_message(self, *args):  # quiet
        return


@dataclass
class Connect:
    slug: str
    status: str = "waiting"  # waiting | done | failed
    auth_url: str = ""
    channel: dict | None = None
    error: str | None = None
    started: str = field(default_factory=utc_now)
    finished: str | None = None
    task: asyncio.Task | None = None
    catcher: _Catcher | None = None

    def out(self) -> ConnectStatusOut:
        return ConnectStatusOut(
            status=self.status,  # type: ignore[arg-type]
            auth_url=self.auth_url if self.status == "waiting" else None,
            channel=ChannelOut(**self.channel) if self.channel else None,
            error=self.error, started=self.started, finished=self.finished,
        )


def host_addresses() -> set[str]:
    """Every address a browser on this machine may come from: loopback and the host's own."""
    out = {"127.0.0.1", "::1", "localhost"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            out.add(str(info[4][0]).split("%")[0])
    except OSError:
        pass
    return out


class ConnectManager:
    """In memory, per server process: the waiting or last consent of each project."""

    def __init__(self, st: ServerState):
        self.st = st
        self._by_slug: dict[str, Connect] = {}
        self._hosts: set[str] | None = None

    def can_connect(self, client_host: str | None) -> bool:
        """Only a browser on the server's machine can finish the consent (the redirect is loopback)."""
        if self._hosts is None:
            self._hosts = host_addresses()
        return bool(client_host) and str(client_host).split("%")[0] in self._hosts

    def status(self, slug: str) -> ConnectStatusOut:
        c = self._by_slug.get(slug)
        return c.out() if c is not None else ConnectStatusOut(status="idle")

    def _publish(self, c: Connect) -> None:
        self.st.bus.publish_event(c.slug, CONNECT_EVENT, c.out().model_dump(mode="json"))

    def start(self, slug: str) -> ConnectStatusOut:
        """Open the loopback listener and hand back Google's URL; the rest runs on the side. A
        consent still waiting for this project is dropped for the new one."""
        client_id, client_secret = youtube.client_creds(self.st.youtube_env)  # YouTubeUnconfigured when not
        self.cancel(slug)
        state = secrets.token_urlsafe(16)
        catcher = _Catcher(state)
        redirect_uri = f"http://{LOOPBACK}:{catcher.server_address[1]}/"
        c = Connect(slug=slug, auth_url=youtube.auth_url(client_id, redirect_uri, state), catcher=catcher)
        self._by_slug[slug] = c
        c.task = asyncio.create_task(self._run(c, client_id, client_secret, redirect_uri))
        self._publish(c)
        return c.out()

    def cancel(self, slug: str) -> bool:
        c = self._by_slug.get(slug)
        if c is None or c.status != "waiting":
            return False
        if c.catcher is not None:
            c.catcher.stop.set()
        if c.task is not None and not c.task.done():
            c.task.cancel()
        c.status, c.error, c.finished = "failed", "cancelled", utc_now()
        self._publish(c)
        return True

    async def _run(self, c: Connect, client_id: str, client_secret: str, redirect_uri: str) -> None:
        catcher = c.catcher
        assert catcher is not None
        transport = self.st.youtube_transport or youtube.urllib_transport
        try:
            try:
                code = await asyncio.wait_for(run_in_threadpool(catcher.wait_for_code), timeout=CONSENT_TIMEOUT_S)
            except asyncio.TimeoutError:
                raise youtube.YouTubeError(0, "Google did not send the browser back within ten minutes; press Connect again") from None
            token = await run_in_threadpool(youtube.exchange_code, client_id, client_secret, code, redirect_uri, transport)
            info = await run_in_threadpool(youtube.fetch_channel, str(token.get("access_token") or ""), transport)
            ch = youtube.save_channel_token(self.st.youtube_env, str(token["refresh_token"]), str(token.get("scope") or youtube.SCOPES), info, connected=utc_now())
            c.channel = ch.info()
            c.status = "done"
        except asyncio.CancelledError:
            if c.status == "waiting":
                c.status, c.error = "failed", "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 - the failure is the status
            c.status = "failed"
            c.error = (str(exc) or exc.__class__.__name__)[-2000:]
        finally:
            catcher.stop.set()  # the listener thread closes the socket within a second
            c.finished = utc_now()
            self._publish(c)

    async def shutdown(self) -> None:
        for slug in list(self._by_slug):
            self.cancel(slug)
