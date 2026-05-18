FROM node:20-bookworm-slim

LABEL org.opencontainers.image.title="Synergix Irys Uploader"
LABEL org.opencontainers.image.description="Microservicio que delega firma de DataItems al SDK oficial @irys/upload-ethereum"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY package.json ./
RUN npm install --omit=dev --no-audit --no-fund

COPY server.js ./

ENV NODE_ENV=production
ENV PORT=8083
EXPOSE 8083

USER node

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:'+ (process.env.PORT||8083) +'/health').then(r => process.exit(r.ok ? 0 : 1)).catch(() => process.exit(1))"

CMD ["node", "server.js"]
