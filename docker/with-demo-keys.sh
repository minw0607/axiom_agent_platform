#!/bin/sh
# Start a container's command with the keyless demo's generated secrets.
#
# For each NAME=value line in the generated file, export it only when the variable is empty,
# so a value set in .env always wins. Then run the container's own command.
#
#   sh with-demo-keys.sh python -m axiom.api
set -eu
file="${AXIOM_DEMO_KEYS_FILE:-/run/axiom-demo/keys.env}"
if [ -f "$file" ]; then
  while IFS='=' read -r name value; do
    case "$name" in
      '' | \#*) continue ;;
    esac
    case "$name" in
      *[!A-Z0-9_]*) continue ;;
    esac
    eval "current=\${$name:-}"
    if [ -z "$current" ]; then
      export "$name=$value"
    fi
  done < "$file"
fi
exec "$@"
