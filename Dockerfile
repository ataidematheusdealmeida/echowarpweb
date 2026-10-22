FROM debian:bookworm-slim

ARG ECHOWARP_VERSION=0.9.2
ARG TARGETARCH
ARG TARGETVARIANT
ARG HLS_JS_VERSION=1

RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      bash \
      ca-certificates \
      curl \
      ffmpeg \
      libasound2 \
      libpulse0 \
      python3 \
      pulseaudio \
      pulseaudio-utils \
      tar \
      adduser; \
    rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "${TARGETARCH:-amd64}/${TARGETVARIANT:-}" in \
      amd64/) asset="EchoWarp_linux_amd64.tar.gz" ;; \
      arm64/) asset="EchoWarp_linux_arm64.tar.gz" ;; \
      arm/v7) asset="EchoWarp_linux_armv7.tar.gz" ;; \
      arm/v6) asset="EchoWarp_linux_armv6.tar.gz" ;; \
      *) echo "Unsupported platform: ${TARGETARCH:-amd64}/${TARGETVARIANT:-}" >&2; exit 1 ;; \
    esac; \
    mkdir -p /opt/echowarp; \
    curl -fsSL "https://github.com/lHumaNl/EchoWarp/releases/download/${ECHOWARP_VERSION}/${asset}" -o /tmp/echowarp.tar.gz; \
    tar -xzf /tmp/echowarp.tar.gz -C /opt/echowarp; \
    chmod +x /opt/echowarp/EchoWarp; \
    rm /tmp/echowarp.tar.gz

COPY web/ /usr/share/nginx/html/
COPY server.py /opt/echowarp-web/server.py
COPY docker-entrypoint.sh /usr/local/bin/echowarp-web

RUN set -eux; \
    addgroup --system echowarp; \
    adduser --system --ingroup echowarp --home /home/echowarp --no-create-home echowarp; \
    mkdir -p /usr/share/nginx/html/vendor /usr/share/nginx/html/hls /opt/echowarp-web; \
    curl -fsSL "https://cdn.jsdelivr.net/npm/hls.js@${HLS_JS_VERSION}/dist/hls.min.js" -o /usr/share/nginx/html/vendor/hls.min.js; \
    mkdir -p \
      /home/echowarp \
      /home/echowarp/.config/echowarp-web \
      /tmp/echowarp-runtime \
      /var/log/echowarp-web; \
    chown -R echowarp:echowarp \
      /home/echowarp \
      /opt/echowarp-web \
      /tmp/echowarp-runtime \
      /usr/share/nginx/html \
      /var/log/echowarp-web; \
    chmod +x /usr/local/bin/echowarp-web

ENV HOME=/home/echowarp \
    XDG_RUNTIME_DIR=/tmp/echowarp-runtime \
    PULSE_SERVER=unix:/tmp/echowarp-runtime/pulse/native

USER echowarp

EXPOSE 3008

ENTRYPOINT ["/usr/local/bin/echowarp-web"]
