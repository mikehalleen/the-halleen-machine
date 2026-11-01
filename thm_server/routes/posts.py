"""Posts: the read shapes, the upload's status, and the project's YouTube channel.

A post is a video with its title and description, drafted for a platform and held unpublished.
Edits and the upload itself go through the command route like every other write (``create_post``,
``set_post_field``, ``delete_post``, ``upload_post``): a landed ``upload_post`` starts the transfer
there, and its progress is the ``post`` event and the GET below.

The channel a project's posts go to is picked in its settings (``set_project_settings
youtube_channel``, through the command route) from the channels connected on this server
(``GET /api/publish/youtube``). Connecting a **new** channel is the one-time consent in
``thm_server/youtube_connect.py``: ``POST .../youtube/connect`` from a browser on the server's
machine, which opens the URL it answers with."""

from __future__ import annotations

from fastapi import APIRouter, Request

from thm_core import youtube

from ..deps import actor_context, state
from ..derive import post_out, post_outs
from ..errors import ApiError
from ..schemas import ChannelOut, ConnectStatusOut, PostOut, PublishStatusOut, UploadStatusOut

router = APIRouter(prefix="/api/projects/{slug}/posts", tags=["posts"])
channel_router = APIRouter(prefix="/api/projects/{slug}/youtube", tags=["posts"])
server_router = APIRouter(prefix="/api/publish", tags=["posts"])


@router.get("", response_model=list[PostOut])
async def list_posts(slug: str, request: Request, bin: str | None = None):
    """Every post, newest first; ``bin`` narrows to the posts drafted from one bin."""
    st = state(request)
    project = st.registry.get(slug)
    out = post_outs(project, st.env)
    return [p for p in out if p.bin == bin] if bin else out


@router.get("/{post_id}", response_model=PostOut)
async def get_post(slug: str, post_id: str, request: Request):
    st = state(request)
    project = st.registry.get(slug)
    post = next((p for p in project.posts if p.id == post_id), None)
    if post is None:
        raise ApiError(404, "unknown_id", f"no post {post_id}", {"type": "post", "id": post_id})
    return post_out(project, post, st.env)


@router.get("/{post_id}/upload", response_model=UploadStatusOut)
async def upload_status(slug: str, post_id: str, request: Request):
    st = state(request)
    project = st.registry.get(slug)
    if not any(p.id == post_id for p in project.posts):
        raise ApiError(404, "unknown_id", f"no post {post_id}", {"type": "post", "id": post_id})
    return st.uploads.status(post_id)


# -- the channel ------------------------------------------------------------------------------------


def _user_only(request: Request) -> None:
    if actor_context(request).actor != "user":
        raise ApiError(403, "wrong_actor", "connecting a YouTube channel is the user's, in the project settings")


def _client_host(request: Request) -> str | None:
    return request.client.host if request.client else None


@channel_router.post("/connect", response_model=ConnectStatusOut)
async def connect_channel(slug: str, request: Request):
    """Start the consent for a new channel: answers Google's URL for the browser to open; the
    redirect lands on this machine, so only a browser here can finish it (``pc_only`` otherwise).
    When it lands, the channel is on the server's list; the project points at it only once the
    user picks it in the settings and saves."""
    st = state(request)
    _user_only(request)
    st.registry.get(slug)  # 404 for an unknown project
    ok, why = youtube.configured(st.youtube_env)
    if not ok:
        raise ApiError(409, "youtube_unconfigured", why)
    if not st.connects.can_connect(_client_host(request)):
        raise ApiError(409, "pc_only", "a new channel is connected from a browser on the server's machine (Google sends the consent back to this PC); pick an already connected channel from here")
    return st.connects.start(slug)


@channel_router.get("/connect", response_model=ConnectStatusOut)
async def connect_status(slug: str, request: Request):
    st = state(request)
    st.registry.get(slug)
    return st.connects.status(slug)


@channel_router.delete("/connect", response_model=ConnectStatusOut)
async def cancel_connect(slug: str, request: Request):
    st = state(request)
    _user_only(request)
    st.registry.get(slug)
    st.connects.cancel(slug)
    return st.connects.status(slug)


@server_router.get("/youtube", response_model=PublishStatusOut)
async def youtube_status(request: Request):
    """Can this server upload to YouTube (the client file is there), which channels are connected,
    and whether this request could connect a new one (it came from the server's machine)."""
    st = state(request)
    ok, why = youtube.configured(st.youtube_env)
    return PublishStatusOut(
        platform="youtube", configured=ok, detail=why,
        channels=[ChannelOut(**c.info()) for c in youtube.channels(st.youtube_env)],
        can_connect=ok and st.connects.can_connect(_client_host(request)),
    )


@server_router.delete("/youtube/channels/{channel_id}", response_model=PublishStatusOut)
async def forget_channel(channel_id: str, request: Request):
    """Forget a channel's token here (the consent itself is revoked at Google). Projects pointing
    at it keep the id and show the channel as gone until they pick another."""
    st = state(request)
    _user_only(request)
    if not youtube.remove_channel(st.youtube_env, channel_id):
        raise ApiError(404, "unknown_channel", f"no channel {channel_id} is connected here", {"channel": channel_id})
    ok, why = youtube.configured(st.youtube_env)
    return PublishStatusOut(
        platform="youtube", configured=ok, detail=why,
        channels=[ChannelOut(**c.info()) for c in youtube.channels(st.youtube_env)],
        can_connect=ok and st.connects.can_connect(_client_host(request)),
    )
