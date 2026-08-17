# ---- Development image: Vite dev server inside Docker ----
# The dev server runs inside the container on the same Docker network as the
# backend, so the Vite proxy target "http://backend:8000" resolves. Source is
# bind-mounted for hot reload; node_modules is kept in an anonymous volume so
# the Linux build is not clobbered by the host's node_modules.
FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

EXPOSE 5173

# vite.config.ts already sets host: true (binds 0.0.0.0) and port 5173, and
# proxies /api -> http://backend:8000.
CMD ["npm", "run", "dev"]
