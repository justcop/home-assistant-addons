#!/usr/bin/with-contenv bash
set -euo pipefail
umask 077
exec /opt/venv/bin/python -m analytics
