# A separate Home Assistant app with a separate add-on slug and /data volume.
# Pin the already-tested production image; fail closed when its layout changes.
FROM ghcr.io/justcop/whatsapp-mcp-amd64:0.3.32
COPY patch-experiment.mjs /tmp/patch-experiment.mjs
RUN node /tmp/patch-experiment.mjs && rm /tmp/patch-experiment.mjs
