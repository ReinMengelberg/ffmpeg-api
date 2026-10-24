FROM python:3.12-alpine

RUN apk add --no-cache ffmpeg \
 && pip install --no-cache-dir starlette==0.41.3 uvicorn==0.32.1 \
 && adduser -D -u 10001 app

WORKDIR /app
COPY app.py .

USER app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD wget -qO- http://127.0.0.1:8080/health || exit 1

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-keep-alive", "75"]
