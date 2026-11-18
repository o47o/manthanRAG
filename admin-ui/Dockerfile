# Admin console (Next.js). The browser talks only to this container; it forwards /api/* to the API.
# API_URL is read at BUILD time (Next.js bakes rewrites in): default = the compose service name.
FROM node:20-alpine AS build
ARG API_URL=http://api:8000
ENV NEXT_PUBLIC_API_URL=${API_URL} NEXT_TELEMETRY_DISABLED=1
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM node:20-alpine
ARG API_URL=http://api:8000
ENV NODE_ENV=production NEXT_PUBLIC_API_URL=${API_URL} NEXT_TELEMETRY_DISABLED=1 PORT=3000
WORKDIR /app
COPY --from=build /app/package.json /app/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/next.config.js ./next.config.js
USER node
EXPOSE 3000
CMD ["npx", "next", "start", "-p", "3000"]
