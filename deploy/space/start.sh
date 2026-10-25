#!/bin/sh
# Entrypoint of the Hugging Face Space (docs/day7-design.md section 2.2): the defaults of
# env.space, the model server on 127.0.0.1 (restarted if it dies), then the app on port 8000.
set -eu

# a default only for what the Space's settings leave unset
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in '' | \#*) continue ;; esac
  name=${line%%=*}
  if [ -z "$(printenv "$name" || true)" ]; then export "$line"; fi
done </app/space/env.space

# Hugging Face sets SPACE_HOST (owner-name.hf.space): the public URL the app pings and serves
if [ -n "${SPACE_HOST:-}" ]; then
  : "${KEEPALIVE_URL:=https://$SPACE_HOST/healthz}"
  : "${CORS_ORIGINS:=https://$SPACE_HOST}"
  : "${MCP_ALLOWED_HOSTS:=$SPACE_HOST,127.0.0.1,localhost}"
  export KEEPALIVE_URL CORS_ORIGINS MCP_ALLOWED_HOSTS
fi

if [ "${USE_MOCK_LLM:-true}" = false ] && [ "${LLM_PROVIDER:-}" = ollama ]; then
  # while it restarts, the claim breaker opens and claims are answered from the rules
  (
    while true; do
      ollama serve || true
      echo "start.sh: ollama exited, restarting in 2 s" >&2
      sleep 2
    done
  ) &
  python /app/space/warm_model.py "$LLM_MODEL" &
fi

exec pundit serve --host 0.0.0.0 --port 8000 --ui /app/ui
