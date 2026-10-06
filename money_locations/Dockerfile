FROM ghcr.io/home-assistant/base:3.22
RUN apk add --no-cache python3 tzdata
ENV PYTHONUNBUFFERED=1 TZ=Europe/London MONEY_DATA=/data
WORKDIR /app
COPY app /app
CMD ["python3", "server.py"]
