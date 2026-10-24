import asyncio
import hmac
import os
import re
import tempfile

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

API_KEY = os.getenv("API_KEY", "")
MAX_BYTES = int(os.getenv("MAX_BYTES", str(2 * 1024**3)))
TIMEOUT_S = int(os.getenv("FFMPEG_TIMEOUT_S", "1800"))
TMP_DIR = os.getenv("TMP_DIR", tempfile.gettempdir())
SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")


def _cleanup(*paths):
    for p in paths:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def _err(msg, code, **extra):
    return JSONResponse({"error": msg, **extra}, status_code=code)


async def to_mp3(request: Request):
    if API_KEY and not hmac.compare_digest(request.headers.get("x-api-key", ""), API_KEY):
        return _err("unauthorized", 401)

    q = request.query_params.get("q", "6")
    bitrate = request.query_params.get("bitrate")
    channels = request.query_params.get("ac", "1")
    if not q.isdigit() or not 0 <= int(q) <= 9 or channels not in ("1", "2"):
        return _err("invalid q or ac", 400)
    if bitrate and not (bitrate.endswith("k") and bitrate[:-1].isdigit()):
        return _err("invalid bitrate", 400)

    name = SAFE_NAME.sub("_", request.query_params.get("filename", "audio.mp3"))[:200]
    if not name.lower().endswith(".mp3"):
        name += ".mp3"

    fd_in, src = tempfile.mkstemp(dir=TMP_DIR, suffix=".in")
    fd_out, dst = tempfile.mkstemp(dir=TMP_DIR, suffix=".mp3")
    os.close(fd_out)

    size = 0
    try:
        with os.fdopen(fd_in, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BYTES:
                    _cleanup(src, dst)
                    return _err("payload too large", 413)
                f.write(chunk)
    except Exception:
        _cleanup(src, dst)
        raise

    if size == 0:
        _cleanup(src, dst)
        return _err("empty body", 400)

    rate = ["-b:a", bitrate] if bitrate else ["-q:a", q]
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-protocol_whitelist", "file",
        "-i", src, "-vn", "-ac", channels,
        "-c:a", "libmp3lame", *rate, "-write_xing", "1", dst,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
    )
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        _cleanup(src, dst)
        return _err("ffmpeg timeout", 504)

    _cleanup(src)
    if proc.returncode != 0:
        _cleanup(dst)
        return _err("ffmpeg failed", 422, detail=err.decode(errors="replace")[-2000:])

    return FileResponse(
        dst, media_type="audio/mpeg", filename=name,
        background=BackgroundTask(_cleanup, dst),
    )


async def health(_: Request):
    return PlainTextResponse("ok")


app = Starlette(routes=[
    Route("/mp3", to_mp3, methods=["POST"]),
    Route("/health", health, methods=["GET"]),
])
