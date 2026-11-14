#!/bin/sh
# Start a container's command with the keyless demo's generated secrets.
#
# AXIOM_DEMO_KEYS names the values this container may have, separated by spaces. An entry
# NAME takes NAME from the generated file; TARGET=SOURCE exports the file's SOURCE as TARGET
# (a tool service reads its own secret as MCP_SHARED_SECRET). Without AXIOM_DEMO_KEYS every
# value in the file is offered. A value is exported only when the variable is empty, so a
# value set in .env always wins. Then the container's own command runs.
#
#   AXIOM_DEMO_KEYS="SUBMITTER_API_KEYS MCP_SHARED_SECRET=GKN_MCP_SECRET" \
#     sh with-demo-keys.sh python -m axiom.api
set -eu
file="${AXIOM_DEMO_KEYS_FILE:-/run/axiom-demo/keys.env}"

valid() {
  case "$1" in
    '' | *[!A-Z0-9_]*) return 1 ;;
  esac
}

# The value of $1 in the generated file, or nothing.
lookup() {
  while IFS='=' read -r name value; do
    if [ "$name" = "$1" ]; then
      printf '%s' "$value"
      return 0
    fi
  done < "$file"
}

offer() {
  valid "$1" || return 0
  eval "current=\${$1:-}"
  if [ -z "$current" ] && [ -n "$2" ]; then
    export "$1=$2"
  fi
}

if [ -f "$file" ]; then
  if [ -n "${AXIOM_DEMO_KEYS+set}" ]; then
    for entry in $AXIOM_DEMO_KEYS; do
      source=${entry#*=}
      valid "$source" || continue
      offer "${entry%%=*}" "$(lookup "$source")"
    done
  else
    while IFS='=' read -r name value; do
      case "$name" in
        '' | \#*) continue ;;
      esac
      offer "$name" "$value"
    done < "$file"
  fi
fi
unset AXIOM_DEMO_KEYS
exec "$@"
