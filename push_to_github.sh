#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

DEFAULT_GH_USER="Harrypotterrrr"
DEFAULT_REPO_NAME="happy-badminton"

echo "============================================================"
echo "  🚀 GitHub Pages Public Web Deployer (happy-badminton)"
echo "============================================================"
echo "Note: For free GitHub accounts, GitHub Pages requires the"
echo "repository to be Public (or GitHub Pro for Private repos)."
echo "If using a Fine-grained PAT, ensure 'Administration: Read/Write',"
echo "'Pages: Read/Write', and 'Contents: Read/Write' are enabled"
echo "(or use a Classic PAT with 'repo' + 'workflow' scopes)."
echo "------------------------------------------------------------"

read -rp "GitHub Username [${DEFAULT_GH_USER}]: " GH_USER
GH_USER="${GH_USER:-$DEFAULT_GH_USER}"

read -rp "Repository Name [${DEFAULT_REPO_NAME}]: " REPO_NAME
REPO_NAME="${REPO_NAME:-$DEFAULT_REPO_NAME}"

echo ""
echo "Choose repository visibility for GitHub Pages:"
echo "  1) Public repository + Free GitHub Pages public URL [Recommended]"
echo "  2) Keep repository Private (GitHub Pages requires GitHub Pro)"
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

echo "Ensuring repository '${GH_USER}/${REPO_NAME}' exists on GitHub..."
curl -sS -o /dev/null \
  -X POST "https://api.github.com/user/repos" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -K <(printf 'header = "Authorization: Bearer %s"\n' "$GH_TOKEN") \
  -d "$(printf '{"name":"%s","private":%s,"description":"NYBC Flushing Badminton Court Availability Calendar"}' "$REPO_NAME" "$IS_PRIVATE")" || true

echo "Setting repository '${GH_USER}/${REPO_NAME}' visibility (private=${IS_PRIVATE})..."
PATCH_STATUS=$(curl -sS -o /tmp/gh_patch_resp.json -w "%{http_code}" \
  -X PATCH "https://api.github.com/repos/${GH_USER}/${REPO_NAME}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -K <(printf 'header = "Authorization: Bearer %s"\n' "$GH_TOKEN") \
  -d "$(printf '{"private":%s}' "$IS_PRIVATE")")

if [[ "$PATCH_STATUS" != "200" ]]; then
  echo "Note: Could not auto-update repo visibility via API (HTTP ${PATCH_STATUS})."
  echo "  If you are on GitHub Free and the repo is Private, switch it to Public at:"
  echo "  https://github.com/${GH_USER}/${REPO_NAME}/settings"
fi
rm -f /tmp/gh_patch_resp.json

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

echo "Pushing latest 'main' branch to ${REMOTE_URL}..."
GH_PUSH_USER="$GH_USER" GH_PUSH_TOKEN="$GH_TOKEN" GIT_ASKPASS="$ASKPASS_SCRIPT" GIT_TERMINAL_PROMPT=0 \
  git push -u origin main

echo "Enabling GitHub Pages on branch 'main'..."
PAGES_STATUS=$(curl -sS -o /tmp/gh_pages_resp.json -w "%{http_code}" \
  -X POST "https://api.github.com/repos/${GH_USER}/${REPO_NAME}/pages" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -K <(printf 'header = "Authorization: Bearer %s"\n' "$GH_TOKEN") \
  -d '{"source":{"branch":"main","path":"/"}}')

if [[ "$PAGES_STATUS" == "201" || "$PAGES_STATUS" == "409" ]]; then
  echo "GitHub Pages is enabled!"
else
  echo "Note: GitHub Pages API returned HTTP ${PAGES_STATUS}."
  echo "  Enable it in 2 clicks at:"
  echo "  https://github.com/${GH_USER}/${REPO_NAME}/settings/pages"
  echo "  (Under 'Build and deployment' -> Branch -> select 'main' and click Save)"
fi
rm -f /tmp/gh_pages_resp.json

PUBLIC_URL="https://$(echo "$GH_USER" | tr '[:upper:]' '[:lower:]').github.io/${REPO_NAME}/"
echo ""
echo "============================================================"
echo "✅ Ready! Share this public link with your crush:"
echo "   🌐 ${PUBLIC_URL}"
echo "   (Takes ~60 seconds after enabling GitHub Pages to go live)"
echo "============================================================"
