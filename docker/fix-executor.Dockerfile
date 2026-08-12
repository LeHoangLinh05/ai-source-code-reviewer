FROM node:20-slim AS node-runtime

FROM python:3.12-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install \
        --no-cache-dir \
        --no-compile \
        bandit \
        pipenv \
        poetry \
        ruff \
        uv \
    && find /usr/local/lib/python3.12/site-packages \
        -type d -name __pycache__ -prune -exec rm -rf '{}' +

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && npm install --global pnpm yarn \
    && npm cache clean --force \
    && rm -rf /root/.npm

WORKDIR /workspace

CMD ["python", "--version"]
