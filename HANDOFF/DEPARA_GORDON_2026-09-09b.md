# DE: Guilherme | PARA: Gordon | RIR-Concierge — Evolution WhatsApp bring-up
**Data:** 2026-09-09 | **Task type:** automation

## Context

O microserviço RIR-Concierge (C:\Users\guilh\rir-concierge) builda e sobe healthy,
mas ainda não tem canal de WhatsApp. Vamos pelo **Evolution API** (WhatsApp Web /
Baileys) em vez da Meta Cloud API, porque o alvo é um **grupo** do festival — a
Meta Cloud API não posta em grupos arbitrários, o Evolution posta.

O compose já tem o profile `evolution` (serviços `evolution-api` + `evolution-db`,
Postgres) e o webhook global já aponta para `http://app:8000/webhook/whatsapp`.
`EVOLUTION_API_KEY` no `.env` = `change-me-evolution-key` (troque depois).

Não mexer em arquivos `*.py`. Não commitar segredos. QR/pairing code é dado
sensível — coloque no output file, não no chat público.

## Tasks

[GORDON_TASK]
name: app-regression-check
description: Confirmar que a stack base ainda sobe healthy antes de adicionar o Evolution
actions:
  - cd C:\Users\guilh\rir-concierge
  - docker compose build app
  - docker compose up -d redis app
  - aguardar health; curl -s http://localhost:8000/healthz  (esperar status ok, redis true)
  - .venv\Scripts\python.exe scripts\simulate_webhook.py --url http://localhost:8000 list-reply --id checkin_palco_sunset  (esperar HTTP 200)
  - .venv\Scripts\python.exe scripts\simulate_webhook.py --url http://localhost:8000 text --body "status"  (esperar HTTP 200)
  - curl -s "http://localhost:8000/checkin?u=test123" | findstr "USER_ID"  (esperar var USER_ID = "test123";)
  - docker compose logs --tail=20 app
stop_on_failure: true
output_file: HANDOFF/RESULTADO_app-regression.md
timeout_sec: 300

[GORDON_TASK]
name: evolution-stack-up
description: Subir Evolution API + Postgres e criar a instância rir-concierge
actions:
  - cd C:\Users\guilh\rir-concierge
  - docker compose --profile evolution up -d
  - aguardar evolution-db healthy (docker compose ps)
  - aguardar evolution-api responder - curl -s -o NUL -w "%{http_code}" http://localhost:8080/  (repetir ate != 000, max 60s)
  - "criar instancia: curl -s -X POST http://localhost:8080/instance/create -H \"apikey: change-me-evolution-key\" -H \"Content-Type: application/json\" -d \"{\\\"instanceName\\\":\\\"rir-concierge\\\",\\\"integration\\\":\\\"WHATSAPP-BAILEYS\\\",\\\"qrcode\\\":true}\""
  - "se a instancia ja existir, seguir: curl -s http://localhost:8080/instance/connect/rir-concierge -H \"apikey: change-me-evolution-key\""
  - capturar o campo base64 (QR) e/ou pairingCode da resposta
  - reportar a URL do manager para pareamento manual - http://localhost:8080/manager  (login com a apikey)
  - "confirmar o webhook da instancia: curl -s http://localhost:8080/webhook/find/rir-concierge -H \"apikey: change-me-evolution-key\"  (deve apontar para http://app:8000/webhook/whatsapp; se nao, setar via POST /webhook/set/rir-concierge)"
stop_on_failure: false
output_file: HANDOFF/RESULTADO_evolution-stack-up.md
timeout_sec: 300

[GORDON_TASK]
name: cloudflared-tunnel
description: Tunel HTTPS publico temporario para o webhook (rodar SO quando Guilherme pedir)
actions:
  - docker run -d --name rir-tunnel --network rir-concierge_default cloudflare/cloudflared:latest tunnel --url http://app:8000
  - sleep 10
  - docker logs rir-tunnel 2>&1 | findstr "trycloudflare.com"
  - reportar a URL https://<algo>.trycloudflare.com
  - "lembrete no output: por essa URL em .env PUBLIC_BASE_URL e rodar docker compose up -d app"
stop_on_failure: false
output_file: HANDOFF/RESULTADO_cloudflared-tunnel.md
timeout_sec: 120

## After Gordon returns

1. Guilherme escaneia o QR do `RESULTADO_evolution-stack-up.md` no **Galaxy S26**
   (WhatsApp > Aparelhos conectados).
2. Descobrir o JID do grupo do festival:
   `curl -s http://localhost:8080/group/fetchAllGroups/rir-concierge?getParticipants=false -H "apikey: change-me-evolution-key"`
3. Editar `.env`:
   - `WHATSAPP_PROVIDER=evolution`
   - `WHATSAPP_GROUP_ID=<JID do grupo, ex 120363...@g.us>`
   - `EVOLUTION_API_KEY=<uma chave forte propria>` (e recriar a instancia com ela)
4. `docker compose up -d app` para recarregar.
5. Claude Code roda o teste end-to-end (precisa tambem do `secrets/oauth_token.json`).

## Ainda pendente e SO Guilherme (nao Docker)
- `scripts/google_oauth_bootstrap.py` -> `secrets/oauth_token.json` (login no navegador)
- Sem isso o scheduler loga erro de calendario a cada 60s (esperado, nao quebra).
