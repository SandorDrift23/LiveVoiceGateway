ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-debian:bookworm
FROM ${BUILD_FROM}

ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-pip \
    && pip3 install "python-dotenv>=1.0.0" "websockets>=13.0" \
    && rm -rf /var/lib/apt/lists/*

COPY *.py /app/live_voice_gateway/
COPY root/ /
RUN chmod a+x /run.sh

WORKDIR /app
CMD ["/run.sh"]
