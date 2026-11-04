#!/usr/bin/env bash
set -euo pipefail

env_file="${1:-.env.local}"
if [[ ! -f "$env_file" ]]; then
  echo "ERROR: admin secret preflight could not find $env_file." >&2
  exit 1
fi

secret_line="$(grep -E '^ADMIN_CSRF_SECRET=' "$env_file" | tail -n 1 || true)"
secret="${secret_line#*=}"
secret="${secret%$'\r'}"
secret="${secret#\"}"
secret="${secret%\"}"
secret="${secret#\'}"
secret="${secret%\'}"

if [[ ${#secret} -ge 32 ]]; then
  unset secret secret_line
  echo "Admin CSRF secret preflight passed (value redacted)."
  exit 0
fi

# Generate and store a 256-bit key locally without printing it to the workflow log.
secret="$(openssl rand -hex 32)"
sed -i '/^ADMIN_CSRF_SECRET=/d' "$env_file"
printf '\nADMIN_CSRF_SECRET=%s\n' "$secret" >> "$env_file"
chmod 600 "$env_file"
unset secret secret_line
echo "Admin CSRF secret was missing or too short; a 256-bit key was generated on the VPS. Admin sessions must sign in again."
