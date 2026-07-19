#!/usr/bin/env bash

set -e

IMAGE_NAME="snapmaker-u1-dev"
BUILD_CONTEXT=".github/dev"

if ! docker build --cache-from "$IMAGE_NAME" -t "$IMAGE_NAME" "$BUILD_CONTEXT"; then
    echo "[!] Docker build failed."
    exit 1
fi

TTY_FLAG=""
[[ -t 0 ]] && TTY_FLAG="-it"

ENV_FLAGS="-e GIT_VERSION -e CI -e PASSWORD"

exec docker run --rm $DOCKER_OPTS $TTY_FLAG $ENV_FLAGS -w "$PWD" -v "$PWD:$PWD" "$IMAGE_NAME" "$@"
