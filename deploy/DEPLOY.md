# Shared deployment (two founders, one server)

Target: any small Ubuntu VPS (Hetzner CX22 / DigitalOcean basic, ~$5–10/mo)
with a domain or subdomain (e.g. `app.yourdomain.com`) pointing at its IP.

## 1. Server prep (once, as root)

```bash
apt update && apt install -y docker.io docker-compose-v2 git
git clone <your-repo-url> /opt/super-app && cd /opt/super-app
```

## 2. Production .env  (`/opt/super-app/.env` — never committed)

```bash
SUPERAPP_DOMAIN=app.yourdomain.com
SUPERAPP_DB_PASSWORD=<long random>
SUPERAPP_API_TOKEN=<harshith's long random token>
SUPERAPP_USER_TOKENS=cofounder:<cofounder's long random token>
SUPERAPP_DEFAULT_USER_ID=harshith
SUPERAPP_ANTHROPIC_API_KEY=sk-ant-...
SUPERAPP_VAULT_KEY=<python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
SUPERAPP_GOOGLE_CLIENT_ID=...
SUPERAPP_GOOGLE_CLIENT_SECRET=...
SUPERAPP_GOOGLE_REDIRECT_URI=https://app.yourdomain.com/v1/gmail/callback
SUPERAPP_GMAIL_SCOPE_TIER=read        # each founder climbs deliberately
SUPERAPP_GMAIL_WEBHOOK_TOKEN=<long random>
SUPERAPP_PLAID_WEBHOOK_TOKEN=<long random>
```

Generate random values with: `openssl rand -hex 24`

In Google Cloud console → your OAuth client → add the new redirect URI
`https://app.yourdomain.com/v1/gmail/callback`, and add the co-founder's
gmail as a test user (Audience page).

## 3. Launch

```bash
cd /opt/super-app
docker compose -f deploy/docker-compose.prod.yml up -d --build
docker compose -f deploy/docker-compose.prod.yml exec api python -m alembic upgrade head
curl https://app.yourdomain.com/health   # {"ok": true}
```

## 4. Crons (per user — each founder's token drives their own runs)

```cron
# morning briefs (7am each founder's timezone; adjust)
0 7 * * *  curl -s -X POST -H "Authorization: Bearer $HARSHITH_TOKEN"  https://app.yourdomain.com/v1/agents/inbox/think
0 7 * * *  curl -s -X POST -H "Authorization: Bearer $COFOUNDER_TOKEN" https://app.yourdomain.com/v1/agents/inbox/think
# outfits, evening nutrition summaries: same pattern per user
# inbox polling every 10 min until Pub/Sub is wired:
*/10 * * * * curl -s -X POST -H "Authorization: Bearer $HARSHITH_TOKEN"  https://app.yourdomain.com/v1/inbox/sync
*/10 * * * * curl -s -X POST -H "Authorization: Bearer $COFOUNDER_TOKEN" https://app.yourdomain.com/v1/inbox/sync
# nightly backup
0 3 * * *  cd /opt/super-app && docker compose -f deploy/docker-compose.prod.yml exec -T db pg_dump -U superapp superapp | gzip > backups/superapp_$(date +\%F).sql.gz
```

## 5. Trust note (say it out loud)

Whoever admins this server can read the database — including the other
founder's triaged mail. Both founders should acknowledge this explicitly.
