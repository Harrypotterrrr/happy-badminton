#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

DEFAULT_REPO_NAME="nybc-flushing-badminton"

echo "============================================================"
echo "  🔒 Private GitHub Repository Exporter"
echo "============================================================"
echo "Note: GitHub requires a Personal Access Token (PAT) instead"
echo "of a account password for Git/API operations."
echo "Create one at: https://github.com/settings/tokens?type=beta"
echo "  (Repository access: All repositories -> Permissions:"
echo "   Administration: Read & write, Contents: Read & write)"
echo "------------------------------------------------------------"

read -rp "GitHub Username: " GH_USER
if [[ -z "$GH_USER" ]]; then
  echo "Error: GitHub username cannot be empty." >&2
  exit 1
fi

read -rp "Repository Name [${DEFAULT_REPO_NAME}]: " REPO_NAME
REPO_NAME="${REPO_NAME:-$DEFAULT_REPO_NAME}"

read -rsp "GitHub Personal Access Token (input hidden): " GH_TOKEN
echo ""
if [[ -z "$GH_TOKEN" ]]; then
  echo "Error: Token cannot be empty." >&2
  exit 1
fi

echo "Creating private repository '${GH_USER}/${REPO_NAME}' on GitHub..."
HTTP_STATUS=$(curl -sS -o /tmp/gh_repo_resp.json -w "%{http_code}" \
  -X POST "https://api.github.com/user/repos" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -K <(printf 'header = "Authorization: Bearer %s"\n' "$GH_TOKEN") \
  -d "$(printf '{"name":"%s","private":true,"description":"NYBC Flushing Badminton Court Availability Crawler & Interactive Calendar"}' "$REPO_NAME")")

if [[ "$HTTP_STATUS" != "201" && "$HTTP_STATUS" != "422" ]]; then
  echo "GitHub API returned HTTP ${HTTP_STATUS}:" >&2
  cat /tmp/gh_repo_resp.json >&2
  rm -f /tmp/gh_repo_resp.json
  exit 1
fi
rm -f /tmp/gh_repo_resp.json

REMOTE_URL="https://github.com/${GH_USER}/${REPO_NAME}.git"
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE_URL"
else
  git remote add origin "$REMOTE_URL"
fi

# Push securely using a temporary GIT_ASKPASS helper so the token is never saved in .git/config
ASKPASS_SCRIPT="$(mktemp)"
chmod 700 "$ASKPASS_SCRIPT"
trap 'rm -f "$ASKPASS_SCRIPT"' EXIT

cat > "$ASKPASS_SCRIPT" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  *Username*) printf '%s\n' "$GH_PUSH_USER" ;;
  *Password*) printf '%s\n' "$GH_PUSH_TOKEN" ;;
esac
EOF

echo "Pushing branch 'main' to ${REMOTE_URL}..."
GH_PUSH_USER="$GH_USER" GH_PUSH_TOKEN="$GH_TOKEN" GIT_ASKPASS="$ASKPASS_SCRIPT" GIT_TERMINAL_PROMPT=0 \
  git push -u origin main

echo ""
echo "✅ Successfully pushed private repository!"
echo "🔗 https://github.com/${GH_USER}/${REPO_NAME}"
