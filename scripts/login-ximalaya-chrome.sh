#!/usr/bin/env bash
set -euo pipefail

CHROME_APP="/Applications/Google Chrome.app"
PROFILE_DIR="${HOME}/.ebook-to-audio/ximalaya-chrome"
LOGIN_URL="https://studio.ximalaya.com/upload?albumId=122326236"

mkdir -p "${PROFILE_DIR}"

open -na "${CHROME_APP}" --args \
  --user-data-dir="${PROFILE_DIR}" \
  "${LOGIN_URL}"

echo "Chrome started for Ximalaya login."
echo "Profile: ${PROFILE_DIR}"
echo "Log in to Ximalaya in this Chrome window, then quit Chrome before starting the publisher Chrome."
