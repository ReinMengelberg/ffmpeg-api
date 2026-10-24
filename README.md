# ffmpeg-api

Tiny internal HTTP service: POST any audio/video file, get it back converted. ffmpeg + Starlette.

## API

All `POST` routes take the raw request body as the input file.

### `POST /mp3`

Shortcut for audio → MP3.

| Query param | Default     | Notes                                   |
|-------------|-------------|-----------------------------------------|
| `q`         | `6`         | LAME VBR quality, 0 (best) – 9 (worst)  |
| `bitrate`   | —           | CBR override, e.g. `64k`                |
| `ac`        | `1`         | Channels: `1` mono, `2` stereo          |
| `filename`  | `audio.mp3` | Sanitized; `.mp3` appended if missing   |

### `POST /convert`

General conversion. Every param is validated against a whitelist; nothing is passed to ffmpeg raw.

| Query param | Notes |
|-------------|-------|
| `format`    | **Required.** `mp3` `wav` `flac` `ogg` `opus` `m4a` `aac` `mp4` `mov` `mkv` `webm` `gif` `jpg` `png` |
| `vcodec`    | `libx264` `libx265` `libvpx` `libvpx-vp9` `libaom-av1` `libsvtav1` `mpeg4` `prores_ks` `gif` `mjpeg` `png` `copy` |
| `acodec`    | `aac` `libmp3lame` `libopus` `libvorbis` `flac` `alac` `pcm_s16le` `pcm_s24le` `copy` |
| `vb` / `ab` | Video / audio bitrate, e.g. `2M`, `128k` |
| `crf`       | 0–63 |
| `preset`    | `ultrafast` … `veryslow` |
| `ar`        | Sample rate, 8000–192000 |
| `ac`        | Channels, 1–8 |
| `ss` / `t`  | Start offset / duration, seconds or `HH:MM:SS(.ms)` |
| `scale`     | `W:H`, `-1`/`-2` keep aspect, e.g. `1280:-2` |
| `fps`       | Frame rate, e.g. `10` |
| `vn` / `an` | `1` drops video / audio |
| `filename`  | Sanitized; extension appended if missing (default `output.<format>`) |

Audio formats drop video automatically; `jpg`/`png` grab a single frame (use `ss` to pick it); `gif` uses a generated palette.

### `POST /probe`

Returns ffprobe's `format` + `streams` as JSON.

### `GET /health`

Always unauthenticated.

Header `X-API-Key` is required on all other routes when `API_KEY` is set.

Responses: `200` file (or JSON for `/probe`) · `400` bad params/empty body · `401` bad key · `413` too large · `422` ffmpeg failed (stderr tail in `detail`) · `504` timeout.

```bash
curl -H "X-API-Key: $KEY" --data-binary @meeting.webm \
  "http://ffmpeg-api:8080/mp3?filename=call.mp3" -o call.mp3

curl -H "X-API-Key: $KEY" --data-binary @clip.mov \
  "http://ffmpeg-api:8080/convert?format=mp4&vcodec=libx264&crf=23&scale=1280:-2" -o clip.mp4

curl -H "X-API-Key: $KEY" --data-binary @meeting.webm \
  "http://ffmpeg-api:8080/convert?format=wav&ar=16000&ac=1&ss=30&t=60" -o chunk.wav
```

## Env

| Var                | Default    |
|--------------------|------------|
| `API_KEY`          | unset (open) |
| `MAX_BYTES`        | `2147483648` (2 GiB) |
| `FFMPEG_TIMEOUT_S` | `1800`     |
| `TMP_DIR`          | `/tmp`     |

## Build & deploy

Pushes to `main` build `linux/amd64` + `linux/arm64` and push to
`ghcr.io/reinmengelberg/ffmpeg-api` with tags `latest` and `sha-<short>`.
A weekly scheduled rebuild picks up ffmpeg/Alpine security fixes.

Deploy with `docker-compose.yml` (Coolify: Service → Docker Compose Empty). No ports or domain; reach it over the internal network.
