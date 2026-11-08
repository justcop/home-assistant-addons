# Experimental wrapper around a third-party MCP implementation.
# Review and pin upstream image digest before production use.
FROM ghcr.io/jacob-tate/vikunja-mcp:latest
USER root
COPY ha-launcher.cjs /app/ha-launcher.cjs
CMD ["node", "/app/ha-launcher.cjs"]
