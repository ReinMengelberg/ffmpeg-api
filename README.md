# mp3-converter

Tiny internal HTTP service: POST any audio/video file, get an MP3 back. ffmpeg + Starlette, ~100 lines.

## API

`POST /mp3` — raw request body is the input file.

| Query param | Default     | Notes                                   |
|-------------|-------------|-----------------------------------------|
| `q`         | `6`         | LAME VBR quality, 0 (best) – 9 (worst)  |
| `bitrate`   | —           | CBR override, e.g. `64k`                |
| `ac`        | `1`         | Channels: `1` mono, `2` stereo          |
| `filename`  | `audio.mp3` | Sanitized; `.mp3` appended if missing   |

Header `X-API-Key` is required when `API_KEY` is set.

`GET /health` — always unauthenticated.

Responses: `200` audio/mpeg · `400` bad params/empty body · `401` bad key · `413` too large · `422` ffmpeg failed (stderr tail in `detail`) · `504` timeout.

```bash
curl -H "X-API-Key: $KEY" --data-binary @meeting.webm \
  "http://mp3-converter:8080/mp3?filename=call.mp3" -o call.mp3
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
`ghcr.io/<owner>/mp3-converter` with tags `latest` and `sha-<short>`.
A weekly scheduled rebuild picks up ffmpeg/Alpine security fixes.

Deploy with `docker-compose.yml` (Coolify: Service → Docker Compose Empty). No ports or domain; reach it over the internal network.
