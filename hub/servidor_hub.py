#!/usr/bin/env python3
"""Portal HTTP mínimo do hub Belmino.

Somente biblioteca padrão do Python 3. Rotas exatas, sem frameworks.
"""

from __future__ import annotations

import ipaddress
import os
import signal
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Sequence

HOST_PADRAO = "127.0.0.1"
PORTA_PADRAO = 8111

CABECALHOS_SEGURANCA: tuple[tuple[str, str], ...] = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    (
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
    ),
    ("X-Frame-Options", "DENY"),
    # HSTS: o hub só é alcançado por HTTPS (o TLS termina no túnel Cloudflare).
    # Fixa o navegador em HTTPS por um ano, subdomínios incluídos.
    ("Strict-Transport-Security", "max-age=31536000; includeSubDomains"),
)

PAGINA_INICIAL = """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>Hub Belmino</title>
  <style>
    :root {
      color-scheme: dark;
      --fundo: #070a10;
      --fundo-card: #0e141f;
      --fundo-card-hover: #131c2b;
      --borda: #223047;
      --borda-hover: #3b5578;
      --texto: #e8eef8;
      --texto-suave: #96a5bb;
      --destaque: #7dd3fc;
    }
    * { box-sizing: border-box; }
    html, body { margin: 0; min-height: 100%; }
    body {
      background:
        radial-gradient(circle at 15% 0%, rgba(56, 189, 248, 0.12), transparent 38rem),
        radial-gradient(circle at 100% 100%, rgba(129, 140, 248, 0.08), transparent 32rem),
        var(--fundo);
      color: var(--texto);
      font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto,
        "Helvetica Neue", Arial, sans-serif;
      line-height: 1.55;
      -webkit-font-smoothing: antialiased;
    }
    main {
      width: min(100% - 2.5rem, 940px);
      margin: 0 auto;
      padding: clamp(3.5rem, 9vw, 7rem) 0 3rem;
    }
    .kicker {
      margin: 0 0 0.8rem;
      color: var(--destaque);
      font-size: 0.76rem;
      font-weight: 700;
      letter-spacing: 0.18em;
      text-transform: uppercase;
    }
    h1 {
      margin: 0;
      font-size: clamp(2.1rem, 6vw, 3.7rem);
      line-height: 1.05;
      letter-spacing: -0.035em;
    }
    .lede {
      max-width: 38rem;
      margin: 1.1rem 0 0;
      color: var(--texto-suave);
      font-size: clamp(1rem, 2vw, 1.1rem);
    }
    .cartoes {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1rem;
      margin-top: clamp(2.5rem, 6vw, 4rem);
    }
    .cartao {
      display: flex;
      min-height: 13rem;
      flex-direction: column;
      padding: 1.4rem;
      border: 1px solid var(--borda);
      border-radius: 1rem;
      background: linear-gradient(145deg, rgba(255, 255, 255, 0.035), transparent 60%),
        var(--fundo-card);
      color: inherit;
      text-decoration: none;
      transition: border-color 160ms ease, background-color 160ms ease,
        transform 160ms ease;
    }
    .cartao:hover, .cartao:focus-visible {
      border-color: var(--borda-hover);
      background-color: var(--fundo-card-hover);
      transform: translateY(-2px);
      outline: none;
    }
    .cartao:focus-visible {
      box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.35);
    }
    .numero {
      color: var(--destaque);
      font-size: 0.75rem;
      font-weight: 700;
      letter-spacing: 0.14em;
    }
    .titulo {
      margin-top: 1.7rem;
      font-size: 1.25rem;
      font-weight: 650;
      letter-spacing: -0.015em;
    }
    .descricao {
      margin-top: 0.45rem;
      color: var(--texto-suave);
      font-size: 0.92rem;
    }
    .abrir {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      margin-top: auto;
      padding-top: 1.4rem;
      color: var(--destaque);
      font-size: 0.82rem;
      font-weight: 650;
    }
    .seta { transition: transform 160ms ease; }
    .cartao:hover .seta, .cartao:focus-visible .seta { transform: translateX(3px); }
    footer {
      margin-top: 3.5rem;
      padding-top: 1.2rem;
      border-top: 1px solid var(--borda);
      color: #6f7f96;
      font-size: 0.78rem;
    }
    @media (prefers-reduced-motion: reduce) {
      * { transition: none !important; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <p class="kicker">Belmino · Hub pessoal</p>
      <h1>Um ponto de entrada.<br>Três caminhos.</h1>
      <p class="lede">Portal enxuto para chegar ao perfil, ao acompanhamento da eleição de 2026
      e ao mapa interativo.</p>
    </header>

    <nav class="cartoes" aria-label="Seções do hub">
      <a class="cartao" href="/sobre">
        <span class="numero">01</span>
        <span class="titulo">Quem sou eu</span>
        <span class="descricao">Perfil, trajetória e formas de contato.</span>
        <span class="abrir">Abrir <span class="seta" aria-hidden="true">→</span></span>
      </a>

      <a class="cartao" href="/eleicao">
        <span class="numero">02</span>
        <span class="titulo">Eleição 2026 ao vivo</span>
        <span class="descricao">Acompanhamento em tempo real do cenário eleitoral.</span>
        <span class="abrir">Abrir <span class="seta" aria-hidden="true">→</span></span>
      </a>

      <a class="cartao" href="/mapa">
        <span class="numero">03</span>
        <span class="titulo">Mapa interativo</span>
        <span class="descricao">Visualização geográfica dos dados e resultados.</span>
        <span class="abrir">Abrir <span class="seta" aria-hidden="true">→</span></span>
      </a>
    </nav>

    <footer>Servidor local · sem rastreamento · sem recursos externos</footer>
  </main>
</body>
</html>
""".encode("utf-8")

CORPO_SAUDE = b'{"ok":true}'
CORPO_NAO_ENCONTRADO = b"not found"

REDIRECIONAMENTOS = {
    "/sobre": "https://eleicao2026.belmino.dev/sobre",
    "/eleicao": "https://eleicao2026.belmino.dev/eleicao",
    "/mapa": "https://eleicao2026.belmino.dev/mapa",
}


def agora_iso() -> str:
    """Timestamp local com fuso, em formato ISO 8601."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ServidorHub(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# --- Espelho de acesso para o painel interno ---------------------------------
# O hub (belmino.dev / www.belmino.dev) não passa pelo nginx do mapa, então sem
# isto o painel de monitoramento nunca veria esses dois domínios. Aqui cada
# resposta final grava UMA linha no MESMO formato do nginx do mapa:
#
#   IP TS STATUS METODO URI HOST BLOQ "REQUISIÇÃO CRUA" UA
#
# Qualquer falha de escrita é engolida: log é conveniência, a página do hub
# nunca pode deixar de responder por causa dele.
LOG_DEFESA = Path(
    os.environ.get(
        "HUB_LOG_DEFESA", "/home/pedro/eleicao2026-mapa/var/defesa/acessos.log"
    )
)
_TRAVA_LOG = threading.Lock()


def _limpar(valor: str, limite: int = 300) -> str:
    texto = (valor or "").replace("\r", " ").replace("\n", " ")
    texto = texto.replace('"', '\\"')
    return texto[:limite]


def registrar_defesa(
    metodo: str, alvo: str, host: str, status: int, ip: str, ua: str, linha_crua: str
) -> None:
    try:
        candidato = (ip or "").strip()
        try:
            ipaddress.ip_address(candidato)
        except ValueError:
            candidato = "-"
        uri = _limpar(alvo.split("?", 1)[0], 200) or "/"
        requisicao = _limpar(linha_crua, 240) or f"{metodo} {alvo} HTTP/1.1"
        registro = (
            f"{candidato} {agora_iso()} {int(status)} {_limpar(metodo, 12)} {uri} "
            f'{_limpar(host, 120) or "-"} 0 "{requisicao}" {_limpar(ua, 180) or "-"}\n'
        )
        with _TRAVA_LOG:
            LOG_DEFESA.parent.mkdir(parents=True, exist_ok=True)
            with LOG_DEFESA.open("a", encoding="utf-8") as arquivo:
                arquivo.write(registro)
    except Exception:  # noqa: BLE001 - log nunca derruba o hub
        pass


class ManipuladorHub(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "HubBelmino/1.0"
    sys_version = ""

    def log_message(self, formato: str, *args: object) -> None:
        mensagem = formato % args
        sys.stderr.write(f"[{agora_iso()}] {self.address_string()} {mensagem}\n")
        sys.stderr.flush()

    def send_response(self, code: int, message: str | None = None) -> None:
        """Acrescenta os headers de segurança a toda resposta final."""
        super().send_response(code, message)
        for nome, valor in CABECALHOS_SEGURANCA:
            self.send_header(nome, valor)

    def parse_request(self) -> bool:
        """Guarda o alvo bruto para que as rotas sejam comparadas por igualdade."""
        try:
            partes = str(self.raw_requestline, "iso-8859-1").rstrip("\r\n").split()
            self._alvo_bruto = partes[1] if len(partes) >= 2 else None
        except (AttributeError, UnicodeDecodeError):
            self._alvo_bruto = None
        return super().parse_request()

    def _enviar(
        self,
        status: int,
        tipo_conteudo: str | None = None,
        corpo: bytes = b"",
        extras: Sequence[tuple[str, str]] = (),
    ) -> None:
        self.send_response(status)
        registrar_defesa(
            metodo=getattr(self, "command", "-") or "-",
            alvo=getattr(self, "path", "-") or "-",
            host=(self.headers.get("Host") if self.headers else "-") or "-",
            status=status,
            ip=(self.headers.get("CF-Connecting-IP") if self.headers else "")
            or self.client_address[0],
            ua=(self.headers.get("User-Agent") if self.headers else "") or "-",
            linha_crua=str(getattr(self, "raw_requestline", b""), "iso-8859-1").rstrip(
                "\r\n"
            ),
        )
        if tipo_conteudo is not None:
            self.send_header("Content-Type", tipo_conteudo)
        for nome, valor in extras:
            self.send_header(nome, valor)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        if corpo and self.command != "HEAD":
            try:
                self.wfile.write(corpo)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _nao_encontrado(self) -> None:
        self._enviar(404, "text/plain; charset=utf-8", CORPO_NAO_ENCONTRADO)

    def _redirecionar(self, destino: str) -> None:
        self._enviar(
            302,
            extras=(
                ("Location", destino),
                ("Cache-Control", "no-store"),
            ),
        )

    def do_GET(self) -> None:  # noqa: N802 - nome exigido por BaseHTTPRequestHandler
        alvo = getattr(self, "_alvo_bruto", None) or self.path
        if alvo is None:
            self._nao_encontrado()
            return
        alvo = alvo.split("?", 1)[0]
        if alvo == "/":
            self._enviar(200, "text/html; charset=utf-8", PAGINA_INICIAL)
        elif alvo == "/health":
            self._enviar(200, "application/json", CORPO_SAUDE)
        elif alvo in REDIRECIONAMENTOS:
            self._redirecionar(REDIRECIONAMENTOS[alvo])
        else:
            self._nao_encontrado()

    # HEAD responde igual ao GET (sem corpo, já tratado em _enviar).
    def do_HEAD(self) -> None:  # noqa: N802 - nome exigido por BaseHTTPRequestHandler
        self.do_GET()

    # Qualquer método diferente de GET/HEAD recebe 404, inclusive os não listados.
    def __getattr__(self, nome: str):
        if nome.startswith("do_"):
            return self._nao_encontrado
        raise AttributeError(nome)


def _ler_inteiro_env(nome: str, padrao: int) -> int:
    valor = os.environ.get(nome, "").strip()
    if not valor:
        return padrao
    try:
        numero = int(valor, 10)
    except ValueError as exc:
        raise SystemExit(f"{nome} inválido: {valor!r} (esperado inteiro)") from exc
    if not 1 <= numero <= 65535:
        raise SystemExit(f"{nome} fora do intervalo 1..65535: {numero}")
    return numero


def main() -> int:
    host = os.environ.get("HUB_HOST", "").strip() or HOST_PADRAO
    porta = _ler_inteiro_env("HUB_PORT", PORTA_PADRAO)

    servidor = ServidorHub((host, porta), ManipuladorHub)
    encerrando = threading.Event()

    def solicitar_encerramento(sinal: int, _frame: object) -> None:
        if encerrando.is_set():
            return
        encerrando.set()
        sys.stderr.write(f"[{agora_iso()}] sinal {sinal} recebido; encerrando\n")
        sys.stderr.flush()
        # shutdown() precisa rodar fora da thread que executa serve_forever().
        threading.Thread(target=servidor.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, solicitar_encerramento)
    signal.signal(signal.SIGINT, solicitar_encerramento)

    sys.stderr.write(
        f"[{agora_iso()}] hub ouvindo em http://{host}:{porta}\n"
    )
    sys.stderr.flush()

    try:
        servidor.serve_forever(poll_interval=0.2)
    finally:
        servidor.server_close()
        sys.stderr.write(f"[{agora_iso()}] servidor encerrado\n")
        sys.stderr.flush()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
