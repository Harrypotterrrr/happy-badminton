#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

DEFAULT_GH_USER="Harrypotterrrr"
DEFAULT_REPO_NAME="happy-badminton"
ENV_DIR="${HOME}/.config/nybc-badminton"
ENV_FILE="${ENV_DIR}/github.env"

SAVED_TOKEN=""
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE" || true
  DEFAULT_GH_USER="${GH_USER:-$DEFAULT_GH_USER}"
  DEFAULT_REPO_NAME="${REPO_NAME:-$DEFAULT_REPO_NAME}"
  SAVED_TOKEN="${GH_TOKEN:-}"
fi

echo "============================================================"
echo "  🚀 GitHub Pages Public Web Deployer (happy-badminton)"
echo "============================================================"
echo "Note: For free GitHub accounts, GitHub Pages requires the"
echo "repository to be Public (or GitHub Pro for Private repos)."
echo "If using a Fine-grained PAT, ensure 'Administration: Read/Write',"
echo "'Pages: Read/Write', 'Actions: Read/Write', and 'Contents: Read/Write'"
echo "are enabled (or use a Classic PAT with 'repo' + 'workflow' scopes)."
echo "------------------------------------------------------------"

read -rp "GitHub Username [${DEFAULT_GH_USER}]: " GH_USER_INPUT
GH_USER="${GH_USER_INPUT:-$DEFAULT_GH_USER}"

read -rp "Repository Name [${DEFAULT_REPO_NAME}]: " REPO_NAME_INPUT
REPO_NAME="${REPO_NAME_INPUT:-$DEFAULT_REPO_NAME}"

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

if [[ -n "$SAVED_TOKEN" ]]; then
  read -rsp "GitHub Personal Access Token [press Enter to reuse saved token]: " GH_TOKEN_INPUT
  echo ""
  GH_TOKEN="${GH_TOKEN_INPUT:-$SAVED_TOKEN}"
else
  read -rsp "GitHub Personal Access Token (input hidden): " GH_TOKEN
  echo ""
fi

if [[ -z "$GH_TOKEN" ]]; then
  echo "Error: Token cannot be empty." >&2
  exit 1
fi

mkdir -p "$ENV_DIR"
chmod 700 "$ENV_DIR"
cat > "$ENV_FILE" <<EOF
GH_USER='${GH_USER}'
REPO_NAME='${REPO_NAME}'
GH_TOKEN='${GH_TOKEN}'
EOF
chmod 600 "$ENV_FILE"

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

echo "Syncing with remote 'main' branch on ${REMOTE_URL}..."
GH_PUSH_USER="$GH_USER" GH_PUSH_TOKEN="$GH_TOKEN" GIT_ASKPASS="$ASKPASS_SCRIPT" GIT_TERMINAL_PROMPT=0 \
  git fetch origin main || true

echo "Crawling latest live NYBC Flushing court availability..."
if python3 nybc_flushing_crawler.py --players 2 3 4 --days 35 --out-dir .; then
  git add index.html nybc_flushing_calendar.html nybc_flushing_calendar.md nybc_flushing_availability.json
  git diff --staged --quiet || git commit -m "chore: update NYBC Flushing court availability"
fi

echo "Pushing latest 'main' branch to ${REMOTE_URL}..."
GH_PUSH_USER="$GH_USER" GH_PUSH_TOKEN="$GH_TOKEN" GIT_ASKPASS="$ASKPASS_SCRIPT" GIT_TERMINAL_PROMPT=0 \
  git push --force-with-lease -u origin main

echo "Refreshing GitHub Actions twice-daily schedule (9 AM & 9 PM EST)..."
curl -sS -o /dev/null -X PUT \
  "https://api.github.com/repos/${GH_USER}/${REPO_NAME}/actions/workflows/update_calendar.yml/disable" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -K <(printf 'header = "Authorization: Bearer %s"\n' "$GH_TOKEN") || true
curl -sS -o /dev/null -X PUT \
  "https://api.github.com/repos/${GH_USER}/${REPO_NAME}/actions/workflows/update_calendar.yml/enable" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -K <(printf 'header = "Authorization: Bearer %s"\n' "$GH_TOKEN") || true

echo "Starting background auto-updater service (twice daily 9am & 9pm EST + instant web Refresh button listener)..."
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
mkdir -p "$SYSTEMD_USER_DIR"
cat > "${SYSTEMD_USER_DIR}/nybc-auto-updater.service" <<EOF
[Unit]
Description=NYBC Flushing Badminton Auto-Updater (Twice Daily 9am & 9pm EST & Web Refresh Listener)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=${REPO_DIR}
ExecStart=/usr/bin/python3 ${REPO_DIR}/nybc_auto_daemon.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF

if systemctl --user daemon-reload >/dev/null 2>&1; then
  systemctl --user enable --now nybc-auto-updater.service >/dev/null 2>&1 || true
  systemctl --user restart nybc-auto-updater.service >/dev/null 2>&1 || true
  echo "Background service 'nybc-auto-updater.service' is active!"
else
  pkill -f "nybc_auto_daemon.py" >/dev/null 2>&1 || true
  nohup python3 "${REPO_DIR}/nybc_auto_daemon.py" >/dev/null 2>&1 &
  echo "Background daemon 'nybc_auto_daemon.py' started!"
fi

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
echo "   (Auto-updates twice a day at 9 AM & 9 PM EST, and clicking"
echo "    '刷新数据' on the website now crawls & syncs automatically!)"
echo "============================================================"
