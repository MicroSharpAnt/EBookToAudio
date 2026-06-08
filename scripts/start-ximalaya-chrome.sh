#!/usr/bin/env bash
set -euo pipefail

CHROME_APP="/Applications/Google Chrome.app"
PROFILE_DIR="${HOME}/.ebook-to-audio/ximalaya-chrome"
DEBUG_PORT="${XIMALAYA_CHROME_DEBUG_PORT:-9222}"
START_URL="${XIMALAYA_CHROME_START_URL:-about:blank}"

mkdir -p "${PROFILE_DIR}"

if [[ -L "${PROFILE_DIR}/SingletonLock" ]]; then
  LOCK_TARGET="$(readlink "${PROFILE_DIR}/SingletonLock" || true)"
  LOCK_PID="${LOCK_TARGET##*-}"
  if [[ "${LOCK_PID}" =~ ^[0-9]+$ ]] && ! kill -0 "${LOCK_PID}" >/dev/null 2>&1; then
    rm -f \
      "${PROFILE_DIR}/SingletonCookie" \
      "${PROFILE_DIR}/SingletonLock" \
      "${PROFILE_DIR}/SingletonSocket"
  fi
fi

open -na "${CHROME_APP}" --args \
  --remote-debugging-port="${DEBUG_PORT}" \
  --remote-debugging-address=127.0.0.1 \
  --user-data-dir="${PROFILE_DIR}" \
  "${START_URL}"

for _ in {1..30}; do
  if curl -fsS "http://127.0.0.1:${DEBUG_PORT}/json/version" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

echo "Chrome started for Ximalaya publishing."
echo "Debug URL: http://127.0.0.1:${DEBUG_PORT}"
echo "Profile: ${PROFILE_DIR}"

if ! curl -fsS "http://127.0.0.1:${DEBUG_PORT}/json/version" >/dev/null 2>&1; then
  echo "Warning: Chrome started, but the debug port is not reachable yet."
  echo "If Chrome is already running, quit Chrome completely and run this script again."
fi
