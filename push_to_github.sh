#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

DEFAULT_REPO_NAME="nybc-flushing-badminton"

echo "============================================================"
echo "  🚀 GitHub Repo & Public Web (GitHub Pages) Deployer"
echo "============================================================"
echo "Create a Personal Access Token (PAT) at:"
echo "  https://github.com/settings/tokens?type=beta"
echo "  - Fine-grained PAT permissions: Administration (Read/Write),"
echo "    Contents (Read/Write), Pages (Read/Write), Workflows (Read/Write)"
echo "  - Or Classic PAT scope: 'repo' + 'workflow'"
echo "------------------------------------------------------------"

read -rp "GitHub Username: " GH_USER
if [[ -z "$GH_USER" ]]; then
  echo "Error: GitHub username cannot be empty." >&2
  exit 1
fi

read -rp "Repository Name [${DEFAULT_REPO_NAME}]: " REPO_NAME
REPO_NAME="${REPO_NAME:-$DEFAULT_REPO_NAME}"

echo ""
echo "Choose repository visibility:"
echo "  1) Public repository + Free GitHub Pages public website [Recommended]"
echo "     (Required for GitHub Pages on free GitHub accounts; contains only public NYBC court times)"
echo "  2) Private repository (GitHub Pages requires GitHub Pro/Team)"
read -rp "Select [1/2, default=1]: " VIS_CHOICE
VIS_CHOICE="${VIS_CHOICE:-1}"

if [[ "$VIS_CHOICE" == "2" ]]; then
  IS_PRIVATE="true"
else
  IS_PRIVATE="false"
fi

read -rsp "GitHub Personal Access Token (input hidden): " GH_TOKEN
echo ""
if [[ -z "$GH_TOKEN" ]]; then
  echo "Error: Token cannot be empty." >&2
  exit 1
fi

echo "Creating/verifying repository '${GH_USER}/${REPO_NAME}' on GitHub..."
HTTP_STATUS=$(curl -sS -o /tmp/gh_repo_resp.json -w "%{http_code}" \
  -X POST "https://api.github.com/user/repos" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -K <(printf 'header = "Authorization: Bearer %s"\n' "$GH_TOKEN") \
  -d "$(printf '{"name":"%s","private":%s,"description":"NYBC Flushing Badminton Court Availability Calendar"}' "$REPO_NAME" "$IS_PRIVATE")")

if [[ "$HTTP_STATUS" != "201" && "$HTTP_STATUS" != "422" ]]; then
  echo "GitHub API returned HTTP ${HTTP_STATUS}:" >&2
  cat /tmp/gh_repo_resp.json >&2
  rm -f /tmp/gh_repo_resp.json
  exit 1
fi
rm -f /tmp/gh_repo_resp.json

# Ensure visibility matches selection if repo already existed
curl -sS -o /dev/null \
  -X PATCH "https://api.github.com/repos/${GH_USER}/${REPO_NAME}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -K <(printf 'header = "Authorization: Bearer %s"\n' "$GH_TOKEN") \
  -d "$(printf '{"private":%s}' "$IS_PRIVATE")" || true

REMOTE_URL="https://github.com/${GH_USER}/${REPO_NAME}.git"
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE_URL"
else
  git remote add origin "$REMOTE_URL"
fi

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

echo "Enabling GitHub Pages on branch 'main'..."
PAGES_STATUS=$(curl -sS -o /tmp/gh_pages_resp.json -w "%{http_code}" \
  -X POST "https://api.github.com/repos/${GH_USER}/${REPO_NAME}/pages" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -K <(printf 'header = "Authorization: Bearer %s"\n' "$GH_TOKEN") \
  -d '{"source":{"branch":"main","path":"/"}}')

rm -f /tmp/gh_pages_resp.json

PUBLIC_URL="https://${GH_USER}.github.io/${REPO_NAME}/"
echo ""
echo "============================================================"
echo "✅ Deployed! Repository & Public Calendar Links:"
echo "   📦 GitHub Repo:    https://github.com/${GH_USER}/${REPO_NAME}"
echo "   🌐 Public Web URL: ${PUBLIC_URL}"
echo "   (Note: GitHub Pages takes ~60 seconds after first push to go live)"
echo "============================================================"
