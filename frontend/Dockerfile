# Node 22 LTS: current pnpm requires >= 22.13, and Next.js 16 needs >= 20.9.
FROM node:22-slim AS base

ENV NEXT_TELEMETRY_DISABLED=1 \
    PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH

# `corepack` ships the pnpm version pinned by the repo rather than a global one.
RUN corepack enable && corepack prepare pnpm@latest --activate

WORKDIR /app

EXPOSE 3000

# --- builder: installs the whole workspace and produces a standalone build ----
FROM base AS builder

# Copy the full workspace. The lockfile alone is not enough: `pnpm install`
# needs every package.json referenced by pnpm-workspace.yaml to resolve links.
COPY . .

RUN pnpm install -r --frozen-lockfile

RUN pnpm --filter rag-kit-web build

# `output: 'standalone'` deliberately omits public/ and .next/static — they are
# expected to come from a CDN. We serve them from the node process instead, so
# copy them into the standalone tree where server.js already looks for them.
# In a pnpm workspace the standalone tree mirrors the repo layout, hence the
# apps/web prefix.
RUN cp -r apps/web/.next/static apps/web/.next/standalone/apps/web/.next/static && \
    if [ -d apps/web/public ]; then \
        cp -r apps/web/public apps/web/.next/standalone/apps/web/public; \
    fi

# --- production: just the standalone bundle, no pnpm store, no sources -------
FROM base AS production

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# node:*-slim already ships an unprivileged `node` user (uid 1000).
COPY --from=builder --chown=node:node /app/apps/web/.next/standalone ./

USER node

# The standalone tree keeps the workspace layout, so server.js sits under
# apps/web rather than at the root.
CMD ["node", "apps/web/server.js"]
