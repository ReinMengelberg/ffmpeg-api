import asyncio
import hmac
import os
import re
import tempfile

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

API_KEY = os.getenv("API_KEY", "")
MAX_BYTES = int(os.getenv("MAX_BYTES", str(2 * 1024**3)))
TIMEOUT_S = int(os.getenv("FFMPEG_TIMEOUT_S", "1800"))
TMP_DIR = os.getenv("TMP_DIR", tempfile.gettempdir())
SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]")

# format: (media type, extra output args)
FORMATS = {
    "mp3": ("audio/mpeg", []),
    "wav": ("audio/wav", []),
    "flac": ("audio/flac", []),
    "ogg": ("audio/ogg", []),
    "opus": ("audio/ogg", []),
    "m4a": ("audio/mp4", ["-movflags", "+faststart"]),
    "aac": ("audio/aac", []),
    "mp4": ("video/mp4", ["-movflags", "+faststart"]),
    "mov": ("video/quicktime", ["-movflags", "+faststart"]),
    "mkv": ("video/x-matroska", []),
    "webm": ("video/webm", []),
    "gif": ("image/gif", []),
    "jpg": ("image/jpeg", ["-frames:v", "1", "-update", "1"]),
    "png": ("image/png", ["-frames:v", "1", "-update", "1"]),
}
VCODECS = {"copy", "libx264", "libx265", "libvpx", "libvpx-vp9", "libaom-av1", "libsvtav1",
           "mpeg4", "prores_ks", "gif", "mjpeg", "png"}
ACODECS = {"copy", "aac", "libmp3lame", "libopus", "libvorbis", "flac", "alac",
           "pcm_s16le", "pcm_s24le"}
PRESETS = {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow",
           "slower", "veryslow"}
TIME = re.compile(r"(\d+:){0,2}\d+(\.\d+)?")
BITRATE = re.compile(r"\d+(\.\d+)?[kM]?")
SCALE = re.compile(r"(-1|-2|\d{1,5}):(-1|-2|\d{1,5})")
FPS = re.compile(r"\d{1,3}(\.\d+)?")


def _cleanup(*paths):
    for p in paths:
        try:
            os.remove(p)
        except FileNotFoundError:
            pass


def _err(msg, code, **extra):
    return JSONResponse({"error": msg, **extra}, status_code=code)


def _authorized(request):
    return not API_KEY or hmac.compare_digest(request.headers.get("x-api-key", ""), API_KEY)


async def _receive(request):
    """Stream the body to a temp file. Returns (path, None) or (None, error response)."""
    fd, src = tempfile.mkstemp(dir=TMP_DIR, suffix=".in")
    size = 0
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BYTES:
                    break
                f.write(chunk)
    except Exception:
        _cleanup(src)
        raise

    if size > MAX_BYTES:
        _cleanup(src)
        return None, _err("payload too large", 413)
    if size == 0:
        _cleanup(src)
        return None, _err("empty body", 400)
    return src, None


async def _run(cmd, stdout=asyncio.subprocess.DEVNULL):
    """Returns (returncode, stdout, stderr), or None on timeout."""
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=stdout, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return None
    return proc.returncode, out, err


async def _transcode(request, in_args, out_args, ext, media_type, default_name):
    name = SAFE_NAME.sub("_", request.query_params.get("filename", default_name))[:200]
    if not name.lower().endswith("." + ext):
        name += "." + ext

    src, error = await _receive(request)
    if error:
        return error

    # ffmpeg picks the muxer from the output extension
    fd_out, dst = tempfile.mkstemp(dir=TMP_DIR, suffix="." + ext)
    os.close(fd_out)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-protocol_whitelist", "file",
        *in_args, "-i", src, *out_args, dst,
    ]
    result = await _run(cmd)
    _cleanup(src)
    if result is None:
        _cleanup(dst)
        return _err("ffmpeg timeout", 504)
    code, _, err = result
    if code != 0:
        _cleanup(dst)
        return _err("ffmpeg failed", 422, detail=err.decode(errors="replace")[-2000:])

    return FileResponse(
        dst, media_type=media_type, filename=name,
        background=BackgroundTask(_cleanup, dst),
    )


def _match(params, key, pattern):
    value = params.get(key)
    if value is not None and not pattern.fullmatch(value):
        raise ValueError(f"invalid {key}")
    return value


def _choice(params, key, choices):
    value = params.get(key)
    if value is not None and value not in choices:
        raise ValueError(f"invalid {key}, expected one of: {', '.join(sorted(choices))}")
    return value


def _int(params, key, lo, hi):
    value = params.get(key)
    if value is not None and not (value.isdigit() and lo <= int(value) <= hi):
        raise ValueError(f"invalid {key}, expected {lo}-{hi}")
    return value


def _flag(params, key):
    return params.get(key, "").lower() in ("1", "true", "yes")


def _convert_args(params):
    """Builds ffmpeg args from validated query params only; nothing is passed through raw."""
    fmt = _choice(params, "format", FORMATS.keys())
    if fmt is None:
        raise ValueError(f"format is required, one of: {', '.join(FORMATS)}")
    media_type, extra = FORMATS[fmt]

    in_args, out_args, filters = [], [], []
    if ss := _match(params, "ss", TIME):
        in_args += ["-ss", ss]
    if t := _match(params, "t", TIME):
        out_args += ["-t", t]

    if _flag(params, "vn") or media_type.startswith("audio/"):
        out_args.append("-vn")
    if _flag(params, "an") or media_type.startswith("image/"):
        out_args.append("-an")

    for key, opt, check in (
        ("vcodec", "-c:v", lambda k: _choice(params, k, VCODECS)),
        ("acodec", "-c:a", lambda k: _choice(params, k, ACODECS)),
        ("vb", "-b:v", lambda k: _match(params, k, BITRATE)),
        ("ab", "-b:a", lambda k: _match(params, k, BITRATE)),
        ("crf", "-crf", lambda k: _int(params, k, 0, 63)),
        ("preset", "-preset", lambda k: _choice(params, k, PRESETS)),
        ("ar", "-ar", lambda k: _int(params, k, 8000, 192000)),
        ("ac", "-ac", lambda k: _int(params, k, 1, 8)),
    ):
        if value := check(key):
            out_args += [opt, value]

    if fps := _match(params, "fps", FPS):
        filters.append(f"fps={fps}")
    if scale := _match(params, "scale", SCALE):
        filters.append(f"scale={scale}")
    if fmt == "gif":
        filters.append("split[a][b];[a]palettegen[p];[b][p]paletteuse")
    if filters:
        out_args += ["-vf", ",".join(filters)]

    return in_args, out_args + extra, fmt, media_type


async def to_mp3(request: Request):
    if not _authorized(request):
        return _err("unauthorized", 401)

    q = request.query_params.get("q", "6")
    bitrate = request.query_params.get("bitrate")
    channels = request.query_params.get("ac", "1")
    if not q.isdigit() or not 0 <= int(q) <= 9 or channels not in ("1", "2"):
        return _err("invalid q or ac", 400)
    if bitrate and not (bitrate.endswith("k") and bitrate[:-1].isdigit()):
        return _err("invalid bitrate", 400)

    rate = ["-b:a", bitrate] if bitrate else ["-q:a", q]
    out_args = ["-vn", "-ac", channels, "-c:a", "libmp3lame", *rate, "-write_xing", "1"]
    return await _transcode(request, [], out_args, "mp3", "audio/mpeg", "audio.mp3")


async def convert(request: Request):
    if not _authorized(request):
        return _err("unauthorized", 401)
    try:
        in_args, out_args, ext, media_type = _convert_args(request.query_params)
    except ValueError as e:
        return _err(str(e), 400)
    return await _transcode(request, in_args, out_args, ext, media_type, f"output.{ext}")


async def probe(request: Request):
    if not _authorized(request):
        return _err("unauthorized", 401)

    src, error = await _receive(request)
    if error:
        return error
    cmd = [
        "ffprobe", "-v", "error", "-protocol_whitelist", "file",
        "-print_format", "json", "-show_format", "-show_streams", src,
    ]
    result = await _run(cmd, stdout=asyncio.subprocess.PIPE)
    _cleanup(src)
    if result is None:
        return _err("ffprobe timeout", 504)
    code, out, err = result
    if code != 0:
        return _err("ffprobe failed", 422, detail=err.decode(errors="replace")[-2000:])
    return Response(out, media_type="application/json")


async def health(_: Request):
    return PlainTextResponse("ok")


app = Starlette(routes=[
    Route("/mp3", to_mp3, methods=["POST"]),
    Route("/convert", convert, methods=["POST"]),
    Route("/probe", probe, methods=["POST"]),
    Route("/health", health, methods=["GET"]),
])
