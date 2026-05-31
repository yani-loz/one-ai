# syntax=docker/dockerfile:1.7
# Multi-stage frontend image. `dev` = Vite hot-reload (used by docker-compose);
# `prod` = static build served by a NON-ROOT nginx with SPA fallback.

# ---- base: node + pnpm via corepack ----
FROM node:22-alpine AS base
ENV PNPM_HOME=/pnpm PATH="/pnpm:$PATH"
RUN corepack enable
WORKDIR /app

# ---- deps: install locked dependencies (cached on lockfile) ----
FROM base AS deps
COPY package.json pnpm-lock.yaml ./
RUN --mount=type=cache,id=pnpm,target=/pnpm/store pnpm install --frozen-lockfile

# ---- dev: source bind-mounted by compose; node_modules masked by an anon volume ----
FROM deps AS dev
COPY . .
EXPOSE 5173
CMD ["pnpm", "dev", "--host"]

# ---- build: produce static assets. VITE_API_URL is inlined at BUILD time, so it
#      must be injected here — default keeps dev parity:
#      docker build --target prod --build-arg VITE_API_URL=https://api.example.com ./frontend
FROM deps AS build
ARG VITE_API_URL=http://localhost:8000
ENV VITE_API_URL=$VITE_API_URL
COPY . .
RUN pnpm build

# ---- prod: non-root nginx (uid 101) serving the built SPA on :8080 ----
FROM nginxinc/nginx-unprivileged:1.27-alpine AS prod
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://localhost:8080/ || exit 1
