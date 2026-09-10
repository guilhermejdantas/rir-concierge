# VM migration - run the morning of 2026-09-11

Goal: move the concierge stack off the laptop onto an always-on GCE VM so the
laptop can stay home, powered off, during the festival (Sep 11-13).

## Why a VM (not Cloud Run)

Evolution API needs a long-lived process with a persistent WhatsApp Web session,
a websocket, and Postgres state. Cloud Run's request-scoped model does not fit.
A tiny `e2-small` (~US$13/mo, or free-tier credit) running the same
`docker compose --profile evolution` is the right shape. A WhatsApp linked
device stays paired ~14 days - covers the festival easily.

## Cost

`e2-small` in southamerica-east1 ~ US$0.02/h ~ US$0.45/day. Stop the VM after
Sep 13 (`gcloud compute instances stop rir-vm --zone southamerica-east1-a`).

## Steps (together, ~20-30 min)

1. `cd C:\Users\guilh\rir-concierge`
2. `git pull`-equivalent not needed (local repo). Make sure `.env` +
   `secrets/oauth_token.json` are current.
3. `.\deploy-vm.ps1 -ProjectId n8n-automation-501418`
   - creates `rir-vm`, installs Docker, uploads the project + `.env` + `secrets/`,
     runs the stack, opens tcp:8080 to your current public IP only.
4. Re-pair WhatsApp (fresh VM = fresh Evolution instance = new QR):
   - open `http://<VM_IP>:8080/manager`, login with `EVOLUTION_API_KEY`
   - instance `rir-concierge` -> scan QR on the Galaxy S26
   - on the **laptop** WhatsApp, remove the old linked device `rir-concierge`
5. Test: send `status` in the festival group -> reply from the VM.
6. Verify the scheduler:
   `gcloud compute ssh rir-vm --zone southamerica-east1-a --command "cd /opt/rir && sudo docker compose logs app | grep -E 'Concierge up|Announcement|poll'"`
7. Shut the laptop stack down: `docker compose down` (local).
8. Laptop can go off. VM runs 24/7.

## Notes / gotchas

- The 08:00 `bomdia` announcement already fired today (Sep 10) from the laptop -
  the file is now past-dated, so the VM will skip it (no double send).
- **Quiz state continuity.** The trivia cursor + scoreboard live in the laptop's
  Redis. A fresh VM = fresh Redis, so Friday's rounds would restart at question 1
  and the Thursday scores would be lost. Before `docker compose down` on the
  laptop, dump the state and restore it on the VM:
  ```
  # laptop
  docker compose exec -T redis redis-cli --raw GET quiz:cursor > quiz_cursor.txt
  docker compose exec -T redis redis-cli --raw GET quiz:scores > quiz_scores.txt
  # VM (after the stack is up)
  gcloud compute scp quiz_cursor.txt quiz_scores.txt rir-vm:/tmp/ --zone southamerica-east1-a
  gcloud compute ssh rir-vm --zone southamerica-east1-a --command \
    "cd /opt/rir && sudo docker compose exec -T redis sh -c 'redis-cli SET quiz:cursor \"$(cat /tmp/quiz_cursor.txt)\"; redis-cli SET quiz:scores \"$(cat /tmp/quiz_scores.txt)\"'"
  ```
- `WHATSAPP_GROUP_ID` (`120363430892898559@g.us`) does not change.
- If your home/phone IP changes, re-open the firewall:
  `gcloud compute firewall-rules update rir-manager --source-ranges=<new-ip>/32`
- The concierge itself needs no public ingress - all WhatsApp traffic is
  Evolution <-> app on the VM's internal docker network, and Evolution -> WhatsApp
  is outbound. Only `/checkin` (browser GPS page) would need public exposure;
  skip unless asked.
- After the festival: `gcloud compute instances delete rir-vm --zone southamerica-east1-a`
  and `gcloud compute firewall-rules delete rir-manager`.
