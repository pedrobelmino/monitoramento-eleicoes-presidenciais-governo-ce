#!/usr/bin/env python3
"""Servidor do painel das Eleições 2026 — atualiza a cada 60 segundos.

Serve uma página que se recarrega sozinha de minuto em minuto, com o painel
gerado a partir dos arquivos oficiais do TSE. A regeneração roda em segundo
plano, então a página nunca espera os ~7 s de busca nos 29 arquivos do TSE.

Uso:  python servidor_eleicao.py [porta]
"""
from __future__ import annotations

import io
import json
import sys
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import painel_eleicao as pe
import estatisticas_ce as ec  # noqa: E402
import painel_html as ph  # noqa: E402
import mapa_governadores as mg  # noqa: E402

PORTA = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
INTERVALO = 60

ESTADO = {"png": None, "html": None, "mapa": None, "dados": None,
          "quando": None, "erro": None, "rodadas": 0}


def regenera() -> None:
    while True:
        t0 = time.time()
        try:
            blocos = pe.monta()
            govs = mg.busca_governadores()
            prop = ec.coleta()   # senador, dep. federal e dep. estadual do Ceará          # 27 corridas de governador
            ESTADO["mapa"] = mg.gera_svg(govs)      # mapa colorido por lado
            ESTADO["html"] = ph.gera(blocos, governadores=govs,
                                     proporcionais=prop)           # painel responsivo
            ESTADO["dados"] = json.dumps(ph.dados(blocos, govs, prop), ensure_ascii=False)
            img = pe.desenha(blocos)           # mantido como reserva estática
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=90)
            ESTADO["png"] = buf.getvalue()
            ESTADO["quando"] = datetime.now()
            ESTADO["erro"] = None
            ESTADO["rodadas"] += 1
            print(f"  [{datetime.now():%H:%M:%S}] painel regerado em {time.time()-t0:.1f}s "
                  f"({len(ESTADO['png'])} bytes)")
        except Exception as e:
            ESTADO["erro"] = f"{type(e).__name__}: {e}"
            print(f"  falha ao regerar: {ESTADO['erro']}")
            traceback.print_exc()
        time.sleep(max(5, INTERVALO - (time.time() - t0)))


PAGINA = """<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta http-equiv="refresh" content="60">
<title>Apuração TSE 2026</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html{height:100%}
body{min-height:100vh;background:#0f1419;color:#e6edf3;
 font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
 display:flex;flex-direction:column;gap:8px;padding:8px}
.barra{background:#1a2029;border:1px solid #2a3441;border-radius:10px;
 padding:8px 14px;display:flex;justify-content:space-between;align-items:center;
 gap:12px;flex-wrap:wrap;flex:0 0 auto}
.t{font-weight:600;font-size:15px;line-height:1.3}
.s{color:#8b98a5;font-size:12px;line-height:1.3}
.pulso{display:inline-block;width:9px;height:9px;border-radius:50%;background:#3fb950;
 margin-right:7px;animation:p 1.6s infinite}
@keyframes p{0%,100%{opacity:1}50%{opacity:.25}}
.painel{flex:1 1 auto;display:flex;min-height:0}
img{width:100%;height:auto;max-height:calc(100vh - 96px);object-fit:contain;
 border-radius:10px;border:1px solid #2a3441;background:#fff;display:block}
.erro{color:#f85149;font-size:13px;text-align:center}
.rodape{flex:0 0 auto;text-align:center;color:#8b98a5;font-size:12px}
@media (min-width:1400px){
 body{padding:10px 14px}
 .t{font-size:17px}
 .s{font-size:13px}
}
@media (max-width:900px){
 .t{font-size:13px}
 .barra{padding:7px 10px}
 img{max-height:none}
}
</style></head><body>
<div class="barra">
  <div class="t">🗳️ Apuração oficial do TSE — Eleições 2026</div>
  <div class="s"><span class="pulso"></span>atualiza sozinho a cada 60 s &nbsp;·&nbsp; __QUANDO__</div>
</div>
<div class="painel"><img src="/painel.png?__TS__" alt="Painel da apuração"></div>
__ERRO__
<div class="rodape">Dados: resultados.tse.jus.br/oficial/ele2026 &nbsp;·&nbsp; F11 = tela cheia</div>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        rota = self.path.split("?")[0]
        if rota == "/dados.json":
            corpo = (ESTADO.get("dados") or "{}").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
            return
        if rota == "/mapa.svg":
            svg = (ESTADO.get("mapa") or "").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(svg)))
            self.end_headers()
            self.wfile.write(svg)
            return
        if rota in ("/", "/index.html"):
            if ESTADO.get("html"):
                corpo = ESTADO["html"].encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(corpo)))
                self.end_headers()
                self.wfile.write(corpo)
                return
            quando = (ESTADO["quando"].strftime("%H:%M:%S")
                      if ESTADO["quando"] else "aguardando primeiro cálculo")
            erro = (f'<div class="erro">Falha na última atualização: {ESTADO["erro"]}</div>'
                    if ESTADO["erro"] else "")
            corpo = (PAGINA.replace("__QUANDO__", f"última atualização {quando}")
                           .replace("__TS__", str(int(time.time())))
                           .replace("__ERRO__", erro)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
        elif rota == "/painel.png":
            png = ESTADO["png"]
            if not png:
                self.send_error(503, "painel ainda sendo gerado")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(png)))
            self.end_headers()
            self.wfile.write(png)
        elif rota == "/saude":
            corpo = (f"ok rodadas={ESTADO['rodadas']} erro={ESTADO['erro']}").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
        else:
            self.send_error(404)

    def log_message(self, *a):  # silencioso
        pass


def main() -> int:
    print(f"servidor do painel na porta {PORTA}, regenerando a cada {INTERVALO}s")
    threading.Thread(target=regenera, daemon=True).start()
    # espera o primeiro painel ficar pronto antes de abrir a porta
    for _ in range(60):
        if ESTADO["png"]:
            break
        time.sleep(1)
    if not ESTADO["png"]:
        print("  aviso: primeiro painel demorou — servindo mesmo assim")
    srv = ThreadingHTTPServer(("0.0.0.0", PORTA), Handler)
    print(f"  no ar em http://localhost:{PORTA}/")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
