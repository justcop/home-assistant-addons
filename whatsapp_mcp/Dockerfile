# The upstream monorepo has *two* independently built Node 24 packages.
# Package them into one HA app, with only the authenticated MCP proxy exposed.
# Pin the commit: never build a floating main branch for a private-message service.
FROM node:24-bookworm-slim AS build
ARG UPSTREAM_COMMIT=5f19883b0d8e2f672d486aa6307f3e945b49532e
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
RUN curl -fLsS --retry 3 "https://github.com/spare-cycles/whatsapp-mcp/archive/${UPSTREAM_COMMIT}.tar.gz" -o /tmp/source.tar.gz \
    && tar -xzf /tmp/source.tar.gz --strip-components=1 -C /src \
    && rm /tmp/source.tar.gz
COPY patch-upstream.mjs review-mcp-register.ts /src/
RUN node /src/patch-upstream.mjs
RUN corepack enable && pnpm install --frozen-lockfile \
    && pnpm -r run build \
    && pnpm deploy --filter whatsapp-api --prod --legacy /opt/out/api \
    && pnpm deploy --filter whatsapp-mcp --prod --legacy /opt/out/mcp

FROM node:24-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg poppler-utils ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/out/api /opt/whatsapp-api
COPY --from=build /opt/out/mcp /opt/whatsapp-mcp
COPY run.mjs archive-server.mjs archive-data.mjs review-data.mjs storage.mjs prune-meta-ai.mjs protocol-retention.mjs /opt/whatsapp-addon/
COPY archive-ui/ /opt/whatsapp-addon/archive-ui/
ENV NODE_ENV=production
EXPOSE 8099
CMD ["node", "/opt/whatsapp-addon/run.mjs"]
