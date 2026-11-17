# syntax=docker/dockerfile:1

FROM node:24-bookworm-slim AS base

ARG CODEX_VERSION=0.167.2

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       procps \
       git \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force \
    && mkdir -p /home/node/.codex \
    && chown -R node:node /home/node/.codex

ENV HOME=/home/node \
    CODEX_HOME=/home/node/.codex \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    NO_COLOR=1

WORKDIR /workspace

USER node

CMD ["codex", "--version"]


# Node.js / JavaScript / TypeScript
FROM base AS runtime-node

USER root

RUN corepack --version

USER node


# Ruby
FROM base AS runtime-ruby

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ruby \
       ruby-dev \
       ruby-bundler \
       ruby-minitest \
       build-essential \
    && rm -rf /var/lib/apt/lists/*

USER node


# Python
FROM base AS runtime-python

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 \
       python3-pip \
       python3-venv \
    && rm -rf /var/lib/apt/lists/*

USER node


# PHP
FROM base AS runtime-php

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       php-cli \
       php-mbstring \
       php-xml \
       php-curl \
       composer \
    && rm -rf /var/lib/apt/lists/*

USER node
