# DE: Guilherme | PARA: Gordon | RIR-Concierge - Evolution WhatsApp (grupo do festival)
**Data:** 2026-09-10 | **Task type:** automation

## Context

O microservico em C:\Users\guilh\rir-concierge ja builda, sobe healthy e esta
tambem no Cloud Run. Agora vamos ligar o canal de WhatsApp do GRUPO do festival
via Evolution API (a Meta Cloud API nao posta em grupos).

O docker-compose.yml tem o profile `evolution` (evolution-api + evolution-db
Postgres) com o webhook global ja apontando para http://app:8000/webhook/whatsapp.
A EVOLUTION_API_KEY esta no .env (linha EVOLUTION_API_KEY=...). NAO imprima a
chave no chat; use-a apenas nos comandos e escreva QR/pairing code somente no
arquivo de saida.

Nao edite arquivos *.py. Nao commite segredos.

## Tasks

[GORDON_TASK]
name: evolution-up
description: Subir Evolution API + Postgres na stack local
actions:
  - cd C:\Users\guilh\rir-concierge
  - docker compose --profile evolution up -d
  - aguardar ate `docker compose ps` mostrar rir-evolution-db healthy
  - "aguardar a API responder: repetir curl -s -o NUL -w \"%{http_code}\" http://localhost:8080/ ate != 000 (max 90s)"
  - docker compose ps
  - docker compose logs --tail=15 evolution-api
stop_on_failure: true
output_file: HANDOFF/RESULTADO_evolution-up.md
timeout_sec: 240

[GORDON_TASK]
name: evolution-instance-qr
description: Criar a instancia rir-concierge e obter o QR de pareamento
actions:
  - cd C:\Users\guilh\rir-concierge
  - "$k = ((Get-Content .env | Select-String '^EVOLUTION_API_KEY=').Line -split '=',2)[1]"
  - "criar instancia: curl.exe -s -X POST http://localhost:8080/instance/create -H \"apikey: $k\" -H \"Content-Type: application/json\" -d '{\\\"instanceName\\\":\\\"rir-concierge\\\",\\\"integration\\\":\\\"WHATSAPP-BAILEYS\\\",\\\"qrcode\\\":true}'"
  - "se retornar que a instancia ja existe: curl.exe -s http://localhost:8080/instance/connect/rir-concierge -H \"apikey: $k\""
  - Do JSON de resposta, extrair o campo `base64` (imagem PNG do QR data URI) e/ou `pairingCode`
  - "Escrever no output_file: o pairingCode em texto, E o base64 completo do QR (o Guilherme cola num visualizador ou usa o pairing code)"
  - "Tambem no output_file: a URL do painel http://localhost:8080/manager (login com a apikey) como alternativa de pareamento visual"
  - "confirmar webhook: curl.exe -s http://localhost:8080/webhook/find/rir-concierge -H \"apikey: $k\"  (deve apontar para http://app:8000/webhook/whatsapp)"
stop_on_failure: false
output_file: HANDOFF/RESULTADO_evolution-instance-qr.md
timeout_sec: 180

## Depois que o Gordon entregar

1. Guilherme escaneia o QR (ou usa o pairingCode) no Galaxy S26:
   WhatsApp > Configuracoes > Aparelhos conectados > Conectar um aparelho.
2. Descobrir o JID do grupo do festival:
   $k = ((Get-Content .env | Select-String '^EVOLUTION_API_KEY=').Line -split '=',2)[1]
   curl.exe -s "http://localhost:8080/group/fetchAllGroups/rir-concierge?getParticipants=false" -H "apikey: $k"
3. Editar .env:
   WHATSAPP_PROVIDER=evolution
   WHATSAPP_GROUP_ID=<id do grupo, formato ...@g.us>
4. docker compose up -d app
5. Mandar "status" no grupo -> o bot responde com o proximo show.

## O que Gordon NAO faz
- Parear o QR (e no celular do Guilherme).
- Login Google/Meta.
