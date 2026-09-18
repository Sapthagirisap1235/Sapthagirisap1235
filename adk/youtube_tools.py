"""YouTube search + real-tab playback, plus a JARVIS-controllable results list.

Why search happens server-side instead of just "open YouTube and search there":
once JARVIS runs on a remote server (Render, a VPS, etc.), it has no browser
and no screen of its own, and youtube.com has no supported way to be
remote-controlled from outside anyway (no API for searching/scrolling/clicking
on someone else's open tab). So JARVIS fetches real results from the YouTube
Data API (server-side, needs YOUTUBE_API_KEY) and shows them as a list in its
own page — but actually PLAYING a video opens it in a real youtube.com tab in
the user's own browser (same mechanism as open_website), not an embedded
player. An embedded iframe can't be made fullscreen, can't be resized, and
generally behaves like a worse YouTube than just... YouTube — there's no
upside to it once you can already open a real tab.
"""
import asyncio
import os

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

# Kept between calls so "play the second one" / "scroll down" can refer to
# the most recent search without the model having to repeat video IDs back
# perfectly. Fine for a single-user assistant; would need to be keyed by
# session for multiple concurrent users.
_last_results: list[dict] = []


async def youtube_search(query: str) -> dict:
    """Search YouTube and show the results as a scrollable list in JARVIS's own page.

    Use this instead of open_website for anything YouTube-related — searching,
    finding a video, "play X on YouTube". Follow up with youtube_play once you
    know which result the user wants — that opens the real video in a new tab.

    Args:
        query: what to search for.
    """
    if not YOUTUBE_API_KEY:
        return {
            "result": "error",
            "message": "YOUTUBE_API_KEY isn't set, so I can't search YouTube for real results.",
        }
    try:
        import httpx

        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "part": "snippet", "type": "video", "maxResults": 8,
                    "q": query, "key": YOUTUBE_API_KEY,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        videos = [
            {
                "video_id": item["id"]["videoId"],
                "title": item["snippet"]["title"],
                "channel": item["snippet"]["channelTitle"],
                "thumbnail": item["snippet"]["thumbnails"]["medium"]["url"],
            }
            for item in data.get("items", [])
            if item.get("id", {}).get("videoId")
        ]
        global _last_results
        _last_results = videos
        # `videos` (with thumbnails + real IDs) goes back as the function_response —
        # server.py relays THAT to the browser to render the results list. The model
        # just needs enough to talk about the results, but the extra fields are
        # harmless for it to see too.
        return {"result": "ok", "count": len(videos), "results": videos}
    except Exception as e:
        return {"result": "error", "message": str(e)}


async def youtube_play(index_or_video_id: str) -> dict:
    """Open one of the videos from the last search results in a real YouTube tab.

    This opens an actual youtube.com/watch page in the user's own browser — not an
    embedded player — so fullscreen, quality settings, captions, etc. all just work
    normally, the same as if they'd clicked the video on youtube.com themselves.

    Args:
        index_or_video_id: either the 1-based number of a result from the last
            youtube_search (e.g. "1" for the first result), or a raw YouTube video ID
            if you already know it some other way.
    """
    video_id = index_or_video_id.strip()
    if video_id.isdigit():
        i = int(video_id) - 1
        if 0 <= i < len(_last_results):
            video_id = _last_results[i]["video_id"]
        else:
            return {"result": "error", "message": f"no result #{video_id} — search first, or check the number."}
    return {"result": "ok", "video_id": video_id, "url": f"https://www.youtube.com/watch?v={video_id}"}


async def scroll_youtube_results(direction: str) -> dict:
    """Scroll the YouTube results list up or down.

    Args:
        direction: "up" or "down".
    """
    direction = direction.strip().lower()
    if direction not in ("up", "down"):
        return {"result": "error", "message": "direction must be 'up' or 'down'"}
    return {"result": "ok", "direction": direction}


def to_youtube_command(name: str, args: dict):
    """Map a function_call (name + args) -> the browser command, same pattern as
    tools.py's to_play_command. youtube_play is NOT handled here on purpose — its
    useful payload (the resolved video URL) comes from its return value, not its
    args, so server.py relays that via the function_response path instead (same
    as open_website).
    """
    if name == "scroll_youtube_results":
        return {"type": "youtube_scroll", "direction": args.get("direction", "down")}
    return None
