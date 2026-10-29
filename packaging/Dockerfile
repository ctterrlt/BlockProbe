# SPDX-License-Identifier: AGPL-3.0-or-later
# Purpose: Optional headless local web UI container.

FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BLOCKPROBE_DATA_DIR=/data \
    BLOCKPROBE_CONFIG=/data/config.toml

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir '.[desktop]' \
    && useradd --create-home --uid 10001 blockprobe \
    && mkdir -p /data \
    && chown blockprobe:blockprobe /data

USER blockprobe
VOLUME ["/data"]
EXPOSE 8550
ENTRYPOINT ["blockprobe", "serve", "--headless", "--host", "0.0.0.0", "--port", "8550"]
