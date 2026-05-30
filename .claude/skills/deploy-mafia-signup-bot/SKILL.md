---
name: deploy-mafia-signup-bot
description: Update the deployed mafia-bot Telegram bot on 176.126.70.131 (Docker Hub image eduard256/mafia-bot) with the latest code.
disable-model-invocation: true
---

# Deploy / update mafia-signup-bot

A Telegram long-polling bot (aiogram) for signing players up to Mafia game
sessions. It runs as a single Docker container named `mafia-bot` on the host
`root@176.126.70.131`, under `/opt/mafia-bot/`, pulling the public Docker Hub
image `eduard256/mafia-bot:latest`. It has no inbound HTTP port (long polling),
so there is no domain, reverse proxy, or health URL — verification is by logs.

## Preconditions
- Deploy from branch `master`, with a clean working tree (commit first).
- Local Docker logged in to Docker Hub as `eduard256` (push access to
  `eduard256/mafia-bot`).
- SSH access to `root@176.126.70.131` (passwordless sudo / root).
- Build for the server's architecture with `--platform linux/amd64`.
- Persistent state lives in the server-side volume `/opt/mafia-bot/data`
  (`events.json`, `users.json`). It is NOT in the image and survives container
  recreation — never delete it during a deploy.
- Secrets live only in `/opt/mafia-bot/.env` on the server (BOT_TOKEN, ADMIN_ID,
  TZ). They are not in git and not in the image.

## Update steps
Run from the project root `/home/user/mafia-signup-bot`.

1. `git push` — make sure the committed code is on GitHub (optional but expected).
2. `SHA=$(git rev-parse --short HEAD)` — authoritative tag for this build.
3. Build the new image with two tags (latest + SHA), amd64:
   ```bash
   docker build --platform linux/amd64 \
     -t eduard256/mafia-bot:latest \
     -t eduard256/mafia-bot:$SHA .
   ```
4. Push both tags (can run in parallel):
   ```bash
   docker push eduard256/mafia-bot:latest
   docker push eduard256/mafia-bot:$SHA
   ```
5. Pull and recreate the container on the server (the compose there always
   references `:latest`):
   ```bash
   ssh root@176.126.70.131 'cd /opt/mafia-bot && docker compose pull && docker compose up -d'
   ```
   If you only changed data on disk (not code), use
   `docker compose restart` instead so the bot reloads `events.json` into memory.

## Verify
```bash
ssh root@176.126.70.131 'docker logs mafia-bot 2>&1 | tail -8; \
  docker inspect -f "{{.State.Status}} restarts={{.RestartCount}}" mafia-bot'
```
Healthy looks like: `Run polling for bot @mafiasexseven_bot`, scheduler `_tick`
running, state `running restarts=0`. Confirm data is intact:
```bash
ssh root@176.126.70.131 'python3 -c "import json;d=json.load(open(\"/opt/mafia-bot/data/events.json\"));print([(e[\"id\"],e[\"capacity\"],len(e[\"participants\"])) for e in d[\"events\"]])"'
```
A `TelegramNetworkError: Request timeout` in the logs means the host cannot
reach api.telegram.org — this server can, so that points to a transient network
issue, not the code.

## Rollback
The previous image SHA tag stays on Docker Hub. To revert, repoint `:latest` to
the last known-good SHA and redeploy:
```bash
# replace <good-sha> with the previous working short SHA
docker pull eduard256/mafia-bot:<good-sha>
docker tag eduard256/mafia-bot:<good-sha> eduard256/mafia-bot:latest
docker push eduard256/mafia-bot:latest
ssh root@176.126.70.131 'cd /opt/mafia-bot && docker compose pull && docker compose up -d'
```
Data in `/opt/mafia-bot/data` is unaffected by a rollback. A timestamped backup
of events.json (`events.json.bak*`) may also exist on the server.
