# Separate Home Assistant trial slug and /data; production image remains pinned.
FROM ghcr.io/justcop/whatsapp-mcp-amd64:0.3.32
COPY trial-observer.mjs trial-recovery.mjs /opt/whatsapp-addon/
COPY trial-recovery-ui.js /opt/whatsapp-addon/archive-ui/trial-recovery-ui.js
COPY patch-experiment.mjs patch-recovery.mjs /tmp/
RUN node /tmp/patch-experiment.mjs \
 && node /tmp/patch-recovery.mjs \
 && rm /tmp/patch-experiment.mjs /tmp/patch-recovery.mjs
