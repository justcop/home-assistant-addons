FROM ghcr.io/home-assistant/base-python:3.15-alpine3.24-2026.11.0
RUN apk add --no-cache tzdata ca-certificates
ENV PYTHONUNBUFFERED=1 TZ=Europe/London MONEY_DATA=/data
WORKDIR /app
COPY app /app
CMD ["python3", "server.py"]
