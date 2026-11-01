"""YouTube: credentials from the environment, the token refresh, and the resumable upload.

Pure functions over an injectable ``transport`` (the same shape ``thm-agent/thm_client.py``
uses: ``(method, url, body, headers) -> (status, body, headers)``), so the server runs them in
a thread and the tests run them against a fake. No file of the project is read here; the
caller hands over the path.

**An API key cannot upload.** ``videos.insert`` needs OAuth 2.0 user consent, so the
environment names files, never keys in ``config.toml``:

- ``THM_YOUTUBE_CLIENT_FILE``: the OAuth client JSON downloaded from Google Cloud console
  (an "installed" / desktop client);
- ``THM_YOUTUBE_TOKEN_DIR`` (optional; default ``youtube_tokens/`` beside the client file): one
  token file per **channel**, named by the channel id, written by the server when the user
  connects a channel from a project's settings (``thm_server/youtube_connect.py``).

A Google account owns several channels (the personal one and brand accounts, each with its own
handle); the consent screen asks which one to authorize and the refresh token is bound to it, so
every channel is its own consent and its own file. A project names the channel its posts go to
(``Project.youtube_channel``); a post copies it when drafted.

Facts the upload is built on: an API project that has not passed Google's audit has its uploads
forced private (the post is private by default anyway); ``videos.insert`` costs 1600 of the
default 10,000 daily quota units, so about six uploads a day; ``status.containsSyntheticMedia``
is the altered-or-synthetic disclosure.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Post

ENV_CLIENT = "THM_YOUTUBE_CLIENT_FILE"
ENV_TOKEN_DIR = "THM_YOUTUBE_TOKEN_DIR"
TOKEN_DIR_NAME = "youtube_tokens"
SCOPE_UPLOAD = "https://www.googleapis.com/auth/youtube.upload"
SCOPE_READ = "https://www.googleapis.com/auth/youtube.readonly"  # channels.list mine=true: the handle
SCOPES = f"{SCOPE_UPLOAD} {SCOPE_READ}"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status"
CHUNK_BYTES = 8 * 1024 * 1024  # a multiple of 256 KiB, as the resumable protocol wants
MAX_RESUMES = 5

Transport = Callable[[str, str, bytes | None, dict[str, str]], tuple[int, bytes, dict[str, str]]]

SETUP_HINT = (
    f"set {ENV_CLIENT} to the OAuth client JSON (Desktop app) from Google Cloud console and restart the server; "
    "then connect a channel from a project's settings, in a browser on the server's machine"
)
CONNECT_HINT = "connect a channel from the project's settings (Project settings > YouTube), in a browser on the server's machine"


class YouTubeUnconfigured(Exception):
    """No usable credentials: the detail says which file is missing and how to fix it."""


class YouTubeError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}" if status else detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Creds:
    client_id: str
    client_secret: str
    refresh_token: str


@dataclass(frozen=True)
class Channel:
    """One connected channel: its token file and what ``channels.list`` said about it."""

    id: str
    title: str = ""
    handle: str = ""  # "@halleen" as YouTube shows it; empty when the channel has none
    connected: str = ""  # when the consent was given
    path: str = ""

    @property
    def label(self) -> str:
        """How the feed and the screens name it: the handle, else the title, else the id."""
        return self.handle or self.title or self.id

    def info(self) -> dict[str, str]:
        return {"id": self.id, "title": self.title, "handle": self.handle, "connected": self.connected}


@dataclass(frozen=True)
class UploadResult:
    video_id: str
    url: str
    studio_url: str


def video_url(video_id: str) -> str:
    return f"https://youtu.be/{video_id}"


def studio_url(video_id: str) -> str:
    """Where the user finishes the video: thumbnail, end screens, cards, publish."""
    return f"https://studio.youtube.com/video/{video_id}/edit"


# -- credentials -------------------------------------------------------------------------------


def read_client_file(path: str | Path) -> tuple[str, str]:
    """``(client_id, client_secret)`` from the console's download (``installed`` or ``web``)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    block = data.get("installed") or data.get("web") or data
    cid, secret = str(block.get("client_id") or "").strip(), str(block.get("client_secret") or "").strip()
    if not cid or not secret:
        raise YouTubeUnconfigured(f"{path} has no client_id/client_secret; download the OAuth client JSON (Desktop app) from Google Cloud console")
    return cid, secret


def _client_path(env: Mapping[str, str]) -> Path:
    client = (env.get(ENV_CLIENT) or "").strip()
    if not client:
        raise YouTubeUnconfigured(f"{ENV_CLIENT} is not set: {SETUP_HINT}")
    if not Path(client).is_file():
        raise YouTubeUnconfigured(f"{ENV_CLIENT} points at {client}, which is not a file")
    return Path(client)


def client_creds(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """``(client_id, client_secret)`` from the environment's client file."""
    env = os.environ if env is None else env
    return read_client_file(_client_path(env))


def token_dir(env: Mapping[str, str] | None = None) -> Path:
    """Where the channel tokens live: ``THM_YOUTUBE_TOKEN_DIR``, else beside the client file."""
    env = os.environ if env is None else env
    given = (env.get(ENV_TOKEN_DIR) or "").strip()
    if given:
        return Path(given)
    return _client_path(env).resolve().parent / TOKEN_DIR_NAME


def configured(env: Mapping[str, str] | None = None) -> tuple[bool, str]:
    """``(True, "")`` when the client file is there and readable, else ``(False, why)``. Whether
    a channel is connected is a separate question (``channels``)."""
    try:
        client_creds(env)
    except YouTubeUnconfigured as exc:
        return False, str(exc)
    return True, ""


def _read_channel_file(path: Path) -> tuple[Channel, str] | None:
    """``(channel, refresh_token)`` or None when the file is not one of ours."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    refresh = str(data.get("refresh_token") or "").strip()
    ch = data.get("channel") if isinstance(data.get("channel"), dict) else {}
    cid = str(ch.get("id") or path.stem).strip()
    if not refresh or not cid:
        return None
    channel = Channel(id=cid, title=str(ch.get("title") or ""), handle=str(ch.get("handle") or ""), connected=str(data.get("connected") or ""), path=str(path))
    return channel, refresh


def channels(env: Mapping[str, str] | None = None) -> list[Channel]:
    """Every connected channel, read fresh from the token directory (connecting one needs no
    restart), by title. Empty when the server is not configured at all."""
    try:
        folder = token_dir(env)
    except YouTubeUnconfigured:
        return []
    if not folder.is_dir():
        return []
    out: list[Channel] = []
    for path in sorted(folder.glob("*.json")):
        read = _read_channel_file(path)
        if read is not None:
            out.append(read[0])
    return sorted(out, key=lambda c: (c.label.lower(), c.id))


def channel(env: Mapping[str, str] | None, channel_id: str) -> Channel | None:
    """One connected channel by id, or None when no token is there for it."""
    if not channel_id:
        return None
    return next((c for c in channels(env) if c.id == channel_id), None)


def channel_info(env: Mapping[str, str] | None, channel_id: str) -> dict[str, str] | None:
    """The ``Env.youtube_channel`` hook: what the commands know about a channel id."""
    found = channel(env, channel_id)
    return found.info() if found is not None else None


def load_creds(env: Mapping[str, str] | None, channel_id: str) -> Creds:
    """The upload credentials for one channel: the client plus that channel's refresh token."""
    env = os.environ if env is None else env
    cid, secret = client_creds(env)
    if not channel_id:
        raise YouTubeUnconfigured(f"no channel to upload to: {CONNECT_HINT}")
    folder = token_dir(env)
    path = folder / f"{channel_id}.json"
    read = _read_channel_file(path) if path.is_file() else None
    if read is None:
        known = ", ".join(c.label for c in channels(env)) or "none connected"
        raise YouTubeUnconfigured(f"channel {channel_id} is not connected on this server (connected: {known}): {CONNECT_HINT}")
    return Creds(client_id=cid, client_secret=secret, refresh_token=read[1])


def save_channel_token(env: Mapping[str, str] | None, refresh_token: str, scope: str, info: Mapping[str, str], *, connected: str) -> Channel:
    """Write (or overwrite) a channel's token file; returns the channel as ``channels`` reads it."""
    folder = token_dir(env)
    folder.mkdir(parents=True, exist_ok=True)
    cid = str(info.get("id") or "").strip()
    if not cid:
        raise YouTubeError(0, "YouTube answered without a channel id")
    ch = {"id": cid, "title": str(info.get("title") or ""), "handle": str(info.get("handle") or "")}
    path = folder / f"{cid}.json"
    path.write_text(json.dumps({"refresh_token": refresh_token, "scope": scope, "channel": ch, "connected": connected}, indent=1), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return Channel(id=cid, title=ch["title"], handle=ch["handle"], connected=connected, path=str(path))


def remove_channel(env: Mapping[str, str] | None, channel_id: str) -> bool:
    """Forget a channel's token. True when a file went; the consent itself is revoked at Google."""
    found = channel(env, channel_id)
    if found is None:
        return False
    try:
        Path(found.path).unlink()
    except OSError:
        return False
    return True


# -- transport -----------------------------------------------------------------------------------


def urllib_transport(method: str, url: str, body: bytes | None, headers: dict[str, str]) -> tuple[int, bytes, dict[str, str]]:
    """The default transport. HTTP errors come back as a status, never an exception."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read(), {k.lower(): v for k, v in resp.headers.items()}
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), {k.lower(): v for k, v in exc.headers.items()}


def _lower(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _error_detail(body: bytes, fallback: str) -> str:
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        text = body.decode("utf-8", "replace").strip()
        return text[:500] or fallback
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        reasons = [e.get("reason") for e in err.get("errors") or [] if isinstance(e, dict) and e.get("reason")]
        msg = str(err.get("message") or fallback)
        return f"{msg} ({', '.join(reasons)})" if reasons else msg
    if isinstance(err, str):
        return f"{err}: {data.get('error_description') or ''}".strip(": ")
    return fallback


# -- the consent ---------------------------------------------------------------------------------


def auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Google's consent page: offline access (a refresh token), the chooser for the account's
    channels, and ``prompt=consent`` so a second channel gets its own token."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str, transport: Transport = urllib_transport) -> dict[str, Any]:
    """The code from the redirect for the tokens. Returns Google's answer (``refresh_token``,
    ``access_token``, ``scope``); refused when it holds no refresh token."""
    body = urllib.parse.urlencode({"code": code, "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri, "grant_type": "authorization_code"}).encode()
    status, out, _ = transport("POST", TOKEN_URL, body, {"Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        raise YouTubeError(status, _error_detail(out, f"the code exchange failed with {status}"))
    token = json.loads(out)
    if not str(token.get("refresh_token") or "").strip():
        raise YouTubeError(status, "Google answered without a refresh_token; remove THM from the account's third-party access and connect again")
    return token


def fetch_channel(access_token: str, transport: Transport = urllib_transport) -> dict[str, str]:
    """``channels.list mine=true`` (1 quota unit): the channel the token is bound to, as
    ``{id, title, handle}``."""
    status, out, _ = transport("GET", CHANNELS_URL, None, {"Authorization": f"Bearer {access_token}"})
    if status != 200:
        raise YouTubeError(status, _error_detail(out, f"could not read the channel ({status})"))
    data = json.loads(out) if out else {}
    items = data.get("items") or []
    if not items:
        raise YouTubeError(status, "the account has no channel the token can see; pick a channel on Google's chooser when connecting")
    item = items[0]
    snippet = item.get("snippet") or {}
    return {"id": str(item.get("id") or ""), "title": str(snippet.get("title") or ""), "handle": str(snippet.get("customUrl") or "")}


# -- the calls -----------------------------------------------------------------------------------


def refresh_access_token(creds: Creds, transport: Transport = urllib_transport) -> str:
    body = urllib.parse.urlencode({"client_id": creds.client_id, "client_secret": creds.client_secret, "refresh_token": creds.refresh_token, "grant_type": "refresh_token"}).encode()
    status, out, _ = transport("POST", TOKEN_URL, body, {"Content-Type": "application/x-www-form-urlencoded"})
    if status != 200:
        detail = _error_detail(out, f"token refresh failed with {status}")
        if "invalid_grant" in detail:
            detail += f" -- the refresh token is dead (revoked, or the consent screen is still in testing and it expired): connect the channel again from the project settings"
        raise YouTubeError(status, detail)
    token = str(json.loads(out).get("access_token") or "")
    if not token:
        raise YouTubeError(status, "token refresh answered without an access_token")
    return token


def metadata_body(post: Post) -> dict[str, Any]:
    """The ``videos.insert`` resource: snippet and status from the post. ``extra.category`` is
    YouTube's categoryId (22 = People & Blogs, 28 = Science & Technology, ...)."""
    snippet: dict[str, Any] = {"title": post.title, "description": post.description}
    if post.tags:
        snippet["tags"] = list(post.tags)
    category = post.extra.get("category") if isinstance(post.extra, dict) else None
    if category:
        snippet["categoryId"] = str(category)
    status: dict[str, Any] = {
        "privacyStatus": post.privacy,
        "selfDeclaredMadeForKids": bool(post.made_for_kids),
        "containsSyntheticMedia": bool(post.synthetic),
    }
    if post.publish_at:
        status["publishAt"] = post.publish_at
    return {"snippet": snippet, "status": status}


def start_resumable(access_token: str, body: dict[str, Any], size: int, mime: str, transport: Transport = urllib_transport) -> str:
    """Open the resumable session; the answer's Location is where the bytes go."""
    payload = json.dumps(body).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": mime,
        "X-Upload-Content-Length": str(size),
    }
    status, out, hdrs = transport("POST", UPLOAD_URL, payload, headers)
    if status != 200:
        raise YouTubeError(status, _error_detail(out, f"could not start the upload ({status})"))
    location = _lower(hdrs).get("location", "")
    if not location:
        raise YouTubeError(status, "the upload session answered without a Location")
    return location


def _resume_offset(upload_url: str, size: int, transport: Transport) -> int | None:
    """Ask the session how much it has: the next byte to send, or None when it is finished."""
    status, _, hdrs = transport("PUT", upload_url, b"", {"Content-Length": "0", "Content-Range": f"bytes */{size}"})
    if status in (200, 201):
        return None
    if status == 308:
        rng = _lower(hdrs).get("range", "")
        return int(rng.rsplit("-", 1)[-1]) + 1 if rng else 0
    raise YouTubeError(status, f"the upload session is gone ({status}); start the upload again")


def upload_file(
    upload_url: str,
    path: str | Path,
    size: int,
    transport: Transport = urllib_transport,
    *,
    on_progress: Callable[[float], None] | None = None,
    chunk_bytes: int = CHUNK_BYTES,
) -> dict[str, Any]:
    """Send the file in chunks; a 308 says keep going, a 5xx or a dropped connection asks the
    session where it stands and resumes from there. Returns the video resource."""
    sent = 0
    resumes = 0
    with open(path, "rb") as f:
        while sent < size:
            f.seek(sent)
            chunk = f.read(chunk_bytes)
            if not chunk:
                raise YouTubeError(0, f"{path} is shorter than its recorded size")
            end = sent + len(chunk) - 1
            headers = {"Content-Length": str(len(chunk)), "Content-Range": f"bytes {sent}-{end}/{size}"}
            try:
                status, out, hdrs = transport("PUT", upload_url, chunk, headers)
            except OSError as exc:
                status, out, hdrs = 0, str(exc).encode(), {}
            if status in (200, 201):
                if on_progress:
                    on_progress(1.0)
                return json.loads(out) if out else {}
            if status == 308:
                rng = _lower(hdrs).get("range", "")
                sent = int(rng.rsplit("-", 1)[-1]) + 1 if rng else end + 1
            elif status == 0 or status >= 500:
                resumes += 1
                if resumes > MAX_RESUMES:
                    raise YouTubeError(status, _error_detail(out, "the upload kept failing") if status else f"the connection kept dropping: {out.decode('utf-8', 'replace')[:200]}")
                offset = _resume_offset(upload_url, size, transport)
                if offset is None:
                    # finished on the server side between the drop and the question: read the resource back
                    status, out, _ = transport("PUT", upload_url, b"", {"Content-Length": "0", "Content-Range": f"bytes */{size}"})
                    return json.loads(out) if out else {}
                sent = offset
            else:
                raise YouTubeError(status, _error_detail(out, f"the upload was refused ({status})"))
            if on_progress:
                on_progress(min(0.99, sent / size))
    # every byte sent and no final answer: ask once more
    status, out, _ = transport("PUT", upload_url, b"", {"Content-Length": "0", "Content-Range": f"bytes */{size}"})
    if status in (200, 201):
        return json.loads(out) if out else {}
    raise YouTubeError(status, _error_detail(out, "the upload did not finish"))


def upload_video(
    creds: Creds,
    post: Post,
    path: str | Path,
    *,
    transport: Transport = urllib_transport,
    on_progress: Callable[[float], None] | None = None,
    mime: str = "video/mp4",
    chunk_bytes: int = CHUNK_BYTES,
) -> UploadResult:
    """The whole thing: refresh, open the session, send the file, read the id back."""
    size = os.path.getsize(path)
    if size <= 0:
        raise YouTubeError(0, f"{path} is empty")
    token = refresh_access_token(creds, transport)
    session = start_resumable(token, metadata_body(post), size, mime, transport)
    resource = upload_file(session, path, size, transport, on_progress=on_progress, chunk_bytes=chunk_bytes)
    video_id = str(resource.get("id") or "")
    if not video_id:
        raise YouTubeError(0, "YouTube answered without a video id")
    return UploadResult(video_id=video_id, url=video_url(video_id), studio_url=studio_url(video_id))
