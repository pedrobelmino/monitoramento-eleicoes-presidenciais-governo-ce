# Hub Belmino — portal de entrada do belmino.dev

Servidor HTTP mínimo (stdlib pura, Python 3) que serve `belmino.dev` e `www.belmino.dev`
pelo túnel nomeado `hermes-tunel-belmino` e manda cada cartão para o destino certo.

Bind: `127.0.0.1:8111` (não exposto — só o cloudflared alcança).

## Rotas

| Rota       | Resposta                                                        |
|------------|-----------------------------------------------------------------|
| `GET /`        | 200 — portal com 3 cartões                                 |
| `GET /sobre`   | 302 → `https://eleicao2026.belmino.dev/`                   |
| `GET /eleicao` | 302 → `https://eleicao2026.belmino.dev/eleicao`            |
| `GET /mapa`    | 302 → `https://eleicao2026.belmino.dev/mapa`               |
| `GET /health`  | 200 — `{"ok":true}`                                        |
| outra rota/método | 404 `not found`                                         |

Cabeçalhos em toda resposta: `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`,
CSP `default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'`,
`X-Frame-Options: DENY`. Redirects com `Cache-Control: no-store`.

Sem dependência externa, sem CDN, sem rastreamento.

## Rodar

Instalação como serviço de usuário:

    mkdir -p ~/.config/systemd/user
    cp hub/systemd/hub-belmino.service ~/.config/systemd/user/
    systemctl --user daemon-reload
    systemctl --user enable --now hub-belmino.service

Teste rápido à mão:

    HUB_PORT=8111 python3 hub/servidor_hub.py

Verificação:

    systemctl --user is-active hub-belmino
    curl -s -o /dev/null -w '%{http_code}\n' localhost:8111/
    curl -sI localhost:8111/mapa | head -3
    curl -s localhost:8111/health

## Ingress (cloudflared)

Em `~/.cloudflared/belmino-eleicao.yml`, antes do `http_status:404`:

    - hostname: www.belmino.dev
      service: http://127.0.0.1:8111
    - hostname: belmino.dev
      service: http://127.0.0.1:8111

Aplicar: `systemctl --user restart hermes-tunel-belmino`.
Rollback: restaurar `belmino-eleicao.yml.bak-antes-hub-*` e reiniciar o mesmo serviço.

## DNS

`belmino.dev` e `www.belmino.dev` são CNAME para
`4b08a3b6-c36d-45a1-a0f9-9d11526af3fc.cfargotunnel.com` (proxy Cloudflare ligado).

    cloudflared tunnel route dns belmino-eleicao belmino.dev
    cloudflared tunnel route dns belmino-eleicao www.belmino.dev
