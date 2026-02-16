#!/bin/bash

set -euo pipefail

usage() {
    echo "Usage: $0 <url> <method> <path> [key=value ...]"
    echo ""
    echo "Arguments:"
    echo "  url     Spoolman base URL (e.g., http://localhost:7912)"
    echo "  method  HTTP method: GET, POST, PATCH"
    echo "  path    API path (e.g., spool/1)"
    echo "  args    Key=value pairs (query params for GET, JSON for POST/PATCH)"
    echo ""
    echo "Examples:"
    echo "  $0 http://localhost:7912 GET spool/1"
    echo "  $0 http://localhost:7912 POST spool filament_id=1 remaining_weight=800"
    echo "  $0 http://localhost:7912 PATCH spool/1 remaining_weight=750"
    exit 1
}

build_query() {
    local query=""
    for arg in "$@"; do
        key="${arg%%=*}"
        value="${arg#*=}"
        value=$(printf '%s' "$value" | jq -sRr @uri)
        if [[ -z "$query" ]]; then
            query="?${key}=${value}"
        else
            query="${query}&${key}=${value}"
        fi
    done
    echo "$query"
}

build_json() {
    local json="{}"
    for arg in "$@"; do
        key="${arg%%=*}"
        value="${arg#*=}"
        if [[ "$value" =~ ^[0-9]+$ ]]; then
            json=$(echo "$json" | jq --arg k "$key" --argjson v "$value" '. + {($k): $v}')
        elif [[ "$value" =~ ^[0-9]+\.[0-9]+$ ]]; then
            json=$(echo "$json" | jq --arg k "$key" --argjson v "$value" '. + {($k): $v}')
        elif [[ "$value" == "true" || "$value" == "false" ]]; then
            json=$(echo "$json" | jq --arg k "$key" --argjson v "$value" '. + {($k): $v}')
        elif [[ "$value" == "null" ]]; then
            json=$(echo "$json" | jq --arg k "$key" '. + {($k): null}')
        else
            json=$(echo "$json" | jq --arg k "$key" --arg v "$value" '. + {($k): $v}')
        fi
    done
    echo "$json"
}

if [[ $# -lt 3 ]]; then
    usage
fi

url="$1"
method="$2"
path="$3"
shift 3

endpoint="${url}/api/v1/${path}"

case "${method^^}" in
    GET)
        query=$(build_query "$@")
        echo ">> GET ${endpoint}${query}"
        curl -s -X GET "${endpoint}${query}" | jq
        ;;
    POST|PATCH)
        json=$(build_json "$@")
        echo ">> ${method^^} $endpoint"
        curl -s -X "${method^^}" -H "Content-Type: application/json" -d "$json" "$endpoint" | jq
        ;;
    *)
        echo "Error: Unsupported method '$method'. Use GET, POST, or PATCH."
        exit 1
        ;;
esac
