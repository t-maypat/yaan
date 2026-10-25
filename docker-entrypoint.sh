#!/bin/sh
# The image runs as an unprivileged user (H1, D108), and ./data is bind-mounted from the host,
# so it must be writable by that user. Say how to fix it instead of failing later mid-write.
set -e
probe="/app/data/.yaan-write-check-$$"
if ! (: > "$probe") 2>/dev/null; then
  echo "yaan: /app/data (./data on the host) is not writable by uid $(id -u)." >&2
  echo "  On Linux, either give it to that uid:   sudo chown -R $(id -u):$(id -g) data" >&2
  echo "  or run as your own user: set YAAN_UID=\$(id -u) and YAAN_GID=\$(id -g) in .env." >&2
  exit 1
fi
rm -f "$probe"
exec "$@"
