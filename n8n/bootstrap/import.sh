#!/bin/sh
# One-shot bootstrap for the n8n container (runs before `n8n start`, against the same database):
#   1. writes the four credentials the workflows use from environment variables (nothing secret is committed),
#   2. imports them and the workflow JSON from /workflows,
#   3. publishes every workflow that has a trigger, so its production webhooks are live when n8n starts.
# Re-running it is safe: import overwrites by id. n8n 2.x runs an error workflow only when it is published too.
set -eu

CREDS=/tmp/relay-credentials.json
cat > "$CREDS" <<JSON
[
  {"id": "relayWebhookAuth", "name": "Relay webhook secret", "type": "httpHeaderAuth",
   "data": {"name": "X-Relay-Token", "value": "${RELAY_WEBHOOK_TOKEN}"}},
  {"id": "relayApiToken", "name": "Relay API token", "type": "httpHeaderAuth",
   "data": {"name": "X-Relay-Token", "value": "${RELAY_API_TOKEN}"}},
  {"id": "mailpitSmtp", "name": "Mailpit SMTP", "type": "smtp",
   "data": {"host": "${SMTP_HOST:-mailpit}", "port": ${SMTP_PORT:-1025}, "secure": false, "disableStartTls": true,
            "user": "", "password": ""}},
  {"id": "relayPostgres", "name": "Relay Postgres", "type": "postgres",
   "data": {"host": "${RELAY_DB_HOST:-postgres}", "port": ${RELAY_DB_PORT:-5432}, "database": "${RELAY_DB_NAME:-relay}",
            "user": "${RELAY_DB_USER:-relay}", "password": "${RELAY_DB_PASSWORD}", "ssl": "disable"}}
]
JSON

n8n import:credentials --input="$CREDS"
rm -f "$CREDS"
n8n import:workflow --separate --input=/workflows
for file in /workflows/*.json; do
  id=$(sed -n 's/^  "id": "\([^"]*\)",$/\1/p' "$file" | head -n 1)
  n8n publish:workflow --id="$id"
done
echo "relay: credentials and workflows imported"
