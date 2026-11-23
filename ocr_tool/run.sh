#!/usr/bin/with-contenv bashio
exec uvicorn app:app --host 0.0.0.0 --port 8099 --no-server-header

