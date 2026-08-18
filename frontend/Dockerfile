# =============================================================================
#  GuruJi web — multistage build
#
#  BASE IMAGE POLICY (read this before bumping a tag)
#  --------------------------------------------------
#  Verified against upstream release data on 2026-08-04:
#    * Node 24 is the Active LTS line (security-supported to April 2028).
#      Node 26 is Current and does not reach LTS until October 2026, so it is
#      the wrong choice for a build that must be reproducible for a year.
#    * nginx 1.30.4 is the current patched stable. It fixes CVE-2026-42533
#      (map+regex buffer overflow), CVE-2026-60005 (slice module memory
#      disclosure) and CVE-2026-56434 (SSI use-after-free). Do not run any
#      1.30.x below .4.
#
#  "Zero CVEs" is not a property anyone can assert in a Dockerfile — feeds
#  update daily and a clean scan this morning is not a clean scan tonight. What
#  this file does instead is pin to the currently-patched lines and give CI
#  something to test. Run, and fail the pipeline on:
#      trivy image --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 guruji-web:latest
#
#  Tags are mutable. Once you have a scan you trust, replace each FROM with its
#  immutable digest:  FROM nginxinc/nginx-unprivileged:1.30.4-alpine@sha256:<digest>
# =============================================================================

# ---------- stage 1: build ---------------------------------------------------
FROM node:24-alpine AS build

WORKDIR /app

# Copy only the manifests first so the dependency layer is cached and does not
# rebuild every time a component changes.
COPY package.json package-lock.json ./

# --ignore-scripts is the important flag here, not an optimisation.
# npm lifecycle scripts execute arbitrary code from every transitive dependency
# at install time; that is the exact mechanism behind the npm supply-chain
# compromises of the last year. This project has no dependency that needs a
# postinstall, so switching them off costs nothing and removes the attack.
# `npm ci` installs the lockfile exactly and fails if it and package.json disagree.
RUN npm ci --ignore-scripts --no-audit --no-fund

COPY tsconfig.json vite.config.ts index.html ./
COPY public ./public
COPY src ./src

# tsc --noEmit runs first: a type error should fail the image build, not ship.
RUN npm run build

# Pre-compress at maximum ratio. nginx then serves the .gz with gzip_static and
# spends no CPU compressing per request — the difference between a container
# that degrades under a traffic spike and one that does not.
RUN find dist -type f \
      \( -name '*.js' -o -name '*.css' -o -name '*.html' \
         -o -name '*.svg' -o -name '*.webmanifest' -o -name '*.txt' \) \
      -exec gzip -9 -k {} \;

# ---------- stage 2: runtime -------------------------------------------------
# nginx-unprivileged, not the official nginx image: the official one starts its
# master process as root and drops privileges for workers. This variant never
# runs as root at all, which is what lets the container run with a read-only
# root filesystem and all capabilities dropped (see docker-compose.yml).
FROM nginxinc/nginx-unprivileged:1.30.4-alpine AS runtime

LABEL org.opencontainers.image.title="guruji-web" \
      org.opencontainers.image.description="GuruJi web client (Preact, static)" \
      org.opencontainers.image.licenses="UNLICENSED"

USER root
# Remove the stock default site so it cannot shadow ours, then hand the config in.
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/nginx.conf
COPY proxy_headers.inc /etc/nginx/conf.d/proxy_headers.inc
COPY --from=build /app/dist /usr/share/nginx/html

# Config must be owned by root and readable by nginx — never writable by the
# user that serves traffic, or an RCE in nginx could rewrite its own config.
RUN chown -R root:root /etc/nginx /usr/share/nginx/html \
 && chmod -R a-w /etc/nginx /usr/share/nginx/html

# No `nginx -t` here on purpose — it was here and it was wrong.
# nginx.conf's upstream block points at "api", a Compose service name that only
# resolves via Docker's embedded DNS once the `edge` network exists. That network
# does not exist during `docker build`, so testing the config here fails on
# hostname resolution every time, not on anything actually broken. This image is
# not meant to run standalone via `docker run` for the same reason — it needs
# Compose's network to find "api" at all.
#
# Real validation happens at container start: if nginx.conf is broken for any
# reason, nginx refuses to start, the container exits, and the HEALTHCHECK below
# catches it within its 5s start_period instead of silently serving nothing.

USER nginx
EXPOSE 8080

# Uses busybox wget, already in the Alpine base — no extra package installed
# just to answer "are you alive". Hits nginx's own /healthz, not the app's
# /health: this check answers "is the web tier serving", and the API's database
# health is checked separately on the api container. Conflating them would make
# a database blip restart the web server for no reason.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q --spider http://127.0.0.1:8080/healthz || exit 1

STOPSIGNAL SIGQUIT
CMD ["nginx", "-g", "daemon off;"]
