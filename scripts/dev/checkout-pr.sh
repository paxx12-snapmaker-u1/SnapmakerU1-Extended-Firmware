#!/usr/bin/env bash

set -eo pipefail

PUSH_USER="${PUSH_USER:-paxx12}"

while getopts "u:" opt; do
  case "$opt" in
    u) PUSH_USER="$OPTARG" ;;
    *)
      echo "usage: $0 [-u <user>] <remote> <pr-number>" >&2
      exit 1
      ;;
  esac
done
shift $((OPTIND - 1))

if [[ $# -ne 2 ]]; then
  echo "usage: $0 [-u <user>] <remote> <pr-number>"
  echo "example: $0 -u paxx12 origin 42"
  exit 1
fi

BASE_REMOTE="$1"
PR_NUMBER="$2"

# Resolve the base repo (`owner/repo`) from the given remote's URL.
REMOTE_URL="$(git remote get-url "$BASE_REMOTE")"
BASE_REPO="$(printf '%s' "$REMOTE_URL" | sed -E 's#^.*github\.com[:/]##; s#\.git$##')"

if [[ -z "$BASE_REPO" ]]; then
  echo "error: could not resolve a GitHub repo from remote '$BASE_REMOTE' ($REMOTE_URL)" >&2
  exit 1
fi

# Use `curl` against the GitHub REST API to resolve the fork the PR comes from.
API_URL="https://api.github.com/repos/$BASE_REPO/pulls/$PR_NUMBER"
AUTH_HEADER=()
if [[ -n "$GITHUB_TOKEN" ]]; then
  AUTH_HEADER=(-H "Authorization: token $GITHUB_TOKEN")
fi

PR_JSON="$(curl -fsSL "${AUTH_HEADER[@]}" -H "Accept: application/vnd.github+json" "$API_URL")"

read -r FORK_ORG BRANCH_NAME CLONE_URL < <(
  printf '%s' "$PR_JSON" | python3 -c '
import json, sys
d = json.load(sys.stdin)
head = d["head"]
repo = head["repo"]
print(repo["owner"]["login"], head["ref"], repo["clone_url"])
'
)

if [[ -z "$FORK_ORG" || -z "$BRANCH_NAME" || -z "$CLONE_URL" ]]; then
  echo "error: could not resolve PR #$PR_NUMBER from $BASE_REPO" >&2
  exit 1
fi

LOCAL_BRANCH="pr-$PR_NUMBER-$FORK_ORG-$BRANCH_NAME"

# Inject `$PUSH_USER@` into the fork URL so pushes authenticate as the maintainer.
PUSH_URL="${CLONE_URL/https:\/\//https://$PUSH_USER@}"

REMOTE_NAME="$FORK_ORG"
if git remote get-url "$REMOTE_NAME" >/dev/null 2>&1; then
  git remote set-url "$REMOTE_NAME" "$PUSH_URL"
else
  git remote add "$REMOTE_NAME" "$PUSH_URL"
fi

echo ">> Fetching $REMOTE_NAME/$BRANCH_NAME..."
git fetch "$REMOTE_NAME" "$BRANCH_NAME"

if git show-ref --verify --quiet "refs/heads/$LOCAL_BRANCH"; then
  git checkout "$LOCAL_BRANCH"
else
  git checkout -b "$LOCAL_BRANCH" "$REMOTE_NAME/$BRANCH_NAME"
fi

# Track the fork branch so `git push` (-u) targets the contributor's branch.
git branch --set-upstream-to "$REMOTE_NAME/$BRANCH_NAME" "$LOCAL_BRANCH"

echo ">> Checked out PR #$PR_NUMBER as '$LOCAL_BRANCH'"
echo ">> Push target: $PUSH_URL ($BRANCH_NAME)"
