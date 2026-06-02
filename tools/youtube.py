"""YouTube extraction tools: transcript and metadata.

Note on the library API: youtube-transcript-api 1.x replaced the old static
``YouTubeTranscriptApi.get_transcript`` / ``list_transcripts`` helpers with an
instance API — ``YouTubeTranscriptApi().fetch(...)`` and ``.list(...)``. This
module uses the current (1.x) API.

Like the vault tools, each tool returns a plain ``dict`` and reports failures as
``{"error": ...}`` rather than raising, so the AI client always gets a useful
answer back.
"""

import re
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from youtube_transcript_api import (
    CouldNotRetrieveTranscript,
    InvalidVideoId,
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)

OEMBED_URL = "https://www.youtube.com/oembed"

# A YouTube video id is always 11 chars from this alphabet.
_ID = r"([0-9A-Za-z_-]{11})"
# Ordered patterns covering watch?v=, youtu.be/, embed/, shorts/, live/, /v/.
_VIDEO_ID_PATTERNS = (
    re.compile(rf"(?:v=|/embed/|/shorts/|/live/|/v/){_ID}"),
    re.compile(rf"youtu\.be/{_ID}"),
)


def parse_video_id(url: str) -> str | None:
    """Extract the 11-character video id from any common YouTube URL form.

    Handles watch?v=, youtu.be/, embed/, shorts/, live/, /v/, extra query
    params (``&t=120``, ``&list=...``), and a bare 11-char id. Returns ``None``
    if no id can be found.
    """
    text = (url or "").strip()
    for pattern in _VIDEO_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    # Allow callers to pass a bare video id directly.
    if re.fullmatch(_ID, text):
        return text
    return None


def register_youtube_tools(mcp: FastMCP) -> None:
    """Register the two YouTube tools on the given FastMCP instance."""

    @mcp.tool()
    def get_youtube_transcript(url: str, language: str = "en") -> dict[str, Any]:
        """Extract the full transcript from a YouTube video.

        Args:
            url: A YouTube video URL in any common form (watch?v=, youtu.be/,
                embed/, with or without extra query params).
            language: Preferred transcript language code (default ``"en"``). If
                that language has no transcript, the first available one is used,
                preferring human-made captions over auto-generated.
        """
        video_id = parse_video_id(url)
        if not video_id:
            return {"error": f"Could not parse a YouTube video id from URL: {url!r}"}

        api = YouTubeTranscriptApi()
        try:
            fetched = api.fetch(video_id, languages=[language])
        except NoTranscriptFound:
            # Preferred language missing — fall back to any available transcript,
            # preferring manually-created over auto-generated.
            try:
                available = list(api.list(video_id))
            except CouldNotRetrieveTranscript as exc:
                return {"error": _transcript_error(video_id, exc)}
            if not available:
                return {
                    "error": (
                        f"No transcript available for video {video_id}. "
                        "Check that the video has captions."
                    )
                }
            chosen = sorted(available, key=lambda t: t.is_generated)[0]
            try:
                fetched = chosen.fetch()
            except CouldNotRetrieveTranscript as exc:
                return {"error": _transcript_error(video_id, exc)}
        except TranscriptsDisabled:
            return {
                "error": (
                    f"Transcripts are disabled for video {video_id}. "
                    "Check whether the video has captions enabled."
                )
            }
        except (VideoUnavailable, InvalidVideoId) as exc:
            return {"error": f"Video not found or unavailable ({video_id}): {exc}"}
        except CouldNotRetrieveTranscript as exc:
            return {"error": _transcript_error(video_id, exc)}

        raw = fetched.to_raw_data()
        segments = [
            {"text": s["text"], "start": s["start"], "duration": s["duration"]}
            for s in raw
        ]
        full_text = " ".join(s["text"] for s in raw)
        return {
            "video_id": video_id,
            "language": fetched.language_code,
            "language_name": fetched.language,
            "is_generated": fetched.is_generated,
            "segment_count": len(segments),
            "segments": segments,
            "full_text": full_text,
        }

    @mcp.tool()
    def get_youtube_metadata(url: str) -> dict[str, Any]:
        """Fetch a YouTube video's title, channel name, channel URL, and thumbnail.

        Uses YouTube's public oEmbed endpoint — no API key required.

        Args:
            url: A YouTube video URL in any common form.
        """
        video_id = parse_video_id(url)
        if not video_id:
            return {"error": f"Could not parse a YouTube video id from URL: {url!r}"}

        try:
            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                resp = client.get(OEMBED_URL, params={"url": url, "format": "json"})
        except httpx.RequestError as exc:
            return {"error": f"Network error contacting YouTube oEmbed: {exc}"}

        if resp.status_code != 200:
            return {
                "error": (
                    f"YouTube oEmbed returned HTTP {resp.status_code} for {url!r}. "
                    "The video may be private, unlisted, or removed."
                ),
                "status_code": resp.status_code,
            }

        try:
            data = resp.json()
        except ValueError:
            return {"error": "YouTube oEmbed returned a non-JSON response."}

        return {
            "video_id": video_id,
            "title": data.get("title"),
            "author_name": data.get("author_name"),
            "author_url": data.get("author_url"),
            "thumbnail_url": data.get("thumbnail_url"),
        }


def _transcript_error(video_id: str, exc: Exception) -> str:
    """A readable message for the various 'could not retrieve' failures
    (includes IP/region blocking, which can happen from cloud hosts)."""
    return (
        f"Could not retrieve a transcript for video {video_id}: {exc} "
        "If this persists, the video may have no captions, or requests from "
        "this network may be blocked by YouTube."
    )
