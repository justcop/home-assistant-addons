FROM node:26-alpine
RUN apk add --no-cache python3
WORKDIR /app
COPY server.cjs /app/server.cjs
CMD ["node", "/app/server.cjs"]
