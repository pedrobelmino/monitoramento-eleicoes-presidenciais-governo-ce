#!/usr/bin/env python3
"""Servidor da PÁGINA PÚBLICA do painel eleitoral (escutando só em 127.0.0.1).

Quem expõe é o router (``router_painel.py``): o prefixo ``/eleicao`` é roteado
para esta porta e — por decisão do Pedro (20/10/2026) — NÃO passa pelo Basic Auth
do painel do roster. É a única rota de conteúdo pública da máquina; as portas
internas (8096/8097/8098) continuam fechadas e autenticadas.

Rotas (o prefixo /eleicao é removido pelo router antes de chegar aqui):

    /              página pública (conta 1 visita anônima)
    /topo.html     fragmento dinâmico do topo (placar) — o JS troca a cada 60 s
    /blocos.html   fragmento dinâmico do resto (insights, regiões, estados, métricas)
    /dados.json    o retrato público em JSON
    /metricas.json contagem anônima de acessos
    /health        "ok" (para monitoração)
    qualquer outra 404

O que NUNCA aparece no HTML/JSON servido: IP, hash de IP, caminho de arquivo,
nome de usuário, token, porta interna, nome de máquina. O conteúdo é exatamente
o que o TSE publica + contas derivadas rotuladas + a contagem anônima agregada.

Uso:  servidor_publico.py [porta]        (padrão 8099; PORT também vale)
"""
from __future__ import annotations

import html
import json
import os
import re
import socket
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

import metricas_publico as mp          # noqa: E402
import snapshot_publico as sp          # noqa: E402

PORTA = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", "8099"))
RETRATO = Path(os.environ.get("PAINEL_PUBLICO_JSON", AQUI / "dados" / "publico.json"))
CONFIG = Path(os.environ.get("PAINEL_CONFIG", Path.home() / ".hermes" / "painel_publico"
                             / "config.json"))
FUSO = timezone(timedelta(hours=-3))

# Intervalo de atualização da coleta: apuração em andamento pede ritmo curto;
# eleição encerrada não precisa ser consultada de minuto em minuto.
INTERVALO_VIVO = int(os.environ.get("PAINEL_INTERVALO_VIVO", "120"))
INTERVALO_PARADO = int(os.environ.get("PAINEL_INTERVALO_PARADO", "900"))

CORES = ["#4f8cff", "#ff5a67", "#f5c542", "#3ddc97"]
TITULO = "Eleição 2026 · Presidente"
# Canal do YouTube do Pedro ("Belmino Souls"). Confirmado pela API do YouTube com o
# token do perfil editor-video (channels.list part=snippet, mine=True):
#   título "Belmino Souls" · customUrl @belmino.c.f · id UCzbzkL9-HtBdHFlqnLeZh8w
# Pode ser sobrescrito em ~/.hermes/painel_publico/config.json -> {"canal_url": "..."}.
CANAL_YOUTUBE = "https://www.youtube.com/@belmino.c.f"

_CACHE: dict[str, object] = {"retrato": None, "mtime": 0.0, "erro": None, "quando": None}


# ------------------------------------------------------------------ utilidades
def esc(valor) -> str:
    return html.escape("" if valor is None else str(valor), quote=True)


def milhar(valor) -> str:
    if valor is None:
        return "—"
    return f"{int(valor):,}".replace(",", ".")


def pct(valor, casas: int = 1) -> str:
    if valor is None:
        return "—"
    return f"{valor:.{casas}f}".replace(".", ",")


def cores_candidatos(nacional: dict) -> dict:
    """nome -> cor, na ordem do placar nacional."""
    mapa = {}
    for i, c in enumerate((nacional or {}).get("candidatos") or []):
        mapa[c.get("nome")] = CORES[i % len(CORES)]
    return mapa


def config() -> dict:
    try:
        dados = json.loads(CONFIG.read_text(encoding="utf-8"))
        return dados if isinstance(dados, dict) else {}
    except Exception:
        return {}


def retrato() -> dict | None:
    """Retrato em memória, recarregado quando o arquivo muda (o gerador grava atômico)."""
    try:
        mtime = RETRATO.stat().st_mtime
    except OSError:
        return _CACHE["retrato"] if isinstance(_CACHE["retrato"], dict) else None
    if _CACHE["retrato"] is not None and mtime == _CACHE["mtime"]:
        return _CACHE["retrato"]          # type: ignore[return-value]
    try:
        dados = json.loads(RETRATO.read_text(encoding="utf-8"))
    except Exception:
        return _CACHE["retrato"] if isinstance(_CACHE["retrato"], dict) else None
    _CACHE["retrato"], _CACHE["mtime"] = dados, mtime
    return dados


# ---------------------------------------------------------------------- CSS/JS
CSS = """
*{box-sizing:border-box}
:root{--fundo:#080c18;--card:#111a2e;--card2:#0e1628;--borda:#1f2b45;--texto:#e8eefc;
--fraco:#93a3c4;--teal:#2ee6c5;--azul:#4f8cff;--vermelho:#ff5a67;--amarelo:#f5c542}
html,body{margin:0;padding:0;background:var(--fundo);color:var(--texto);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
line-height:1.5;-webkit-text-size-adjust:100%}
body{background-image:radial-gradient(900px 500px at 15% -10%,#16305c55,transparent 70%),
radial-gradient(700px 420px at 95% 0%,#0d4a3f55,transparent 70%);background-attachment:fixed}
.envelope{max-width:1080px;margin:0 auto;padding:22px 16px 56px}
header.topo{padding:6px 0 18px;border-bottom:1px solid var(--borda);margin-bottom:22px}
.selo{display:inline-flex;align-items:center;gap:7px;font-size:12px;letter-spacing:.08em;
text-transform:uppercase;color:var(--teal);background:#0f3a33;border:1px solid #1c6155;
padding:5px 11px;border-radius:999px;font-weight:600}
.selo i{width:7px;height:7px;border-radius:50%;background:var(--teal);display:inline-block}
h1{font-size:clamp(26px,6vw,40px);line-height:1.1;margin:14px 0 6px;letter-spacing:-.02em}
h1 span{background:linear-gradient(92deg,var(--teal),var(--azul));-webkit-background-clip:text;
background-clip:text;color:transparent}
.sub{color:var(--fraco);font-size:15px;margin:0}
.pilha{display:grid;gap:16px;margin:22px 0}
.card{background:linear-gradient(180deg,var(--card),var(--card2));border:1px solid var(--borda);
border-radius:18px;padding:18px 18px 20px;box-shadow:0 14px 34px #0000 0}
.card h2{font-size:15px;letter-spacing:.06em;text-transform:uppercase;color:var(--fraco);
margin:0 0 14px;font-weight:700}
.placar{display:grid;gap:12px;grid-template-columns:1fr}
@media(min-width:680px){.placar{grid-template-columns:1fr 1fr}}
.cand{background:#0b1424;border:1px solid var(--borda);border-radius:16px;padding:16px;
position:relative;overflow:hidden}
.cand::before{content:"";position:absolute;inset:0 auto 0 0;width:5px;background:var(--cor)}
.cand .nome{font-weight:700;font-size:17px;display:flex;justify-content:space-between;gap:8px}
.cand .nome em{font-style:normal;color:var(--fraco);font-size:12px;font-weight:600;
border:1px solid var(--borda);border-radius:6px;padding:1px 6px;align-self:center}
.cand .num{font-size:clamp(38px,10vw,56px);font-weight:800;letter-spacing:-.03em;line-height:1;
margin:10px 0 4px;color:var(--cor)}
.cand .votos{color:var(--fraco);font-size:14px}
.barra{height:16px;border-radius:999px;overflow:hidden;display:flex;background:#0a1120;
border:1px solid var(--borda);margin-top:16px}
.barra div{height:100%}
.legenda{display:flex;gap:16px;flex-wrap:wrap;color:var(--fraco);font-size:13px;margin-top:10px}
.legenda b{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}
.aviso{border-left:4px solid var(--amarelo);background:#2a210a;border-radius:12px;
padding:12px 14px;color:#f7e7b6;font-size:14px}
.insight{display:flex;gap:12px;align-items:flex-start;padding:12px 0;border-bottom:1px dashed #1d2942}
.insight:last-child{border-bottom:0}
.insight p{margin:0;font-size:15px}
.tag{font-size:10px;letter-spacing:.09em;text-transform:uppercase;font-weight:800;
padding:3px 8px;border-radius:999px;white-space:nowrap;margin-top:3px}
.t-dado{background:#0d3b3a;color:#5cf0d6;border:1px solid #1b6b66}
.t-conta{background:#1a2c4f;color:#9dc0ff;border:1px solid #2f4c85}
.t-estimativa{background:#3a2a08;color:#f7cf7a;border:1px solid #7a5a12}
.tabela{width:100%;border-collapse:collapse;font-size:14.5px}
.tabela th{text-align:left;color:var(--fraco);font-size:11.5px;letter-spacing:.07em;
text-transform:uppercase;font-weight:700;padding:6px 8px;border-bottom:1px solid var(--borda)}
.tabela td{padding:10px 8px;border-bottom:1px solid #16203441}
.tabela tr:last-child td{border-bottom:0}
.tabela .dir{text-align:right}
.tabela tr.destaque{background:#12233d66}
.mini{height:6px;border-radius:999px;background:#0a1120;overflow:hidden;min-width:52px}
.mini span{display:block;height:100%;background:var(--teal)}
.grade-ufs{display:grid;gap:8px;grid-template-columns:repeat(auto-fill,minmax(112px,1fr))}
.uf{background:#0b1424;border:1px solid var(--borda);border-radius:12px;padding:9px 10px}
.uf .sigla{font-weight:800;font-size:14px;display:flex;justify-content:space-between}
.uf .sigla em{font-style:normal;font-size:11px;color:var(--fraco)}
.uf .lider{font-size:11.5px;color:var(--fraco);margin-top:3px;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}
.uf .faixa{height:4px;border-radius:999px;margin-top:7px;background:#16223a}
.uf .faixa span{display:block;height:100%;border-radius:999px;background:var(--cor)}
.figura{width:100%;height:auto;display:block}
.metricas{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(140px,1fr))}
.metrica{background:#0b1424;border:1px solid var(--borda);border-radius:14px;padding:14px}
.metrica .rot{color:var(--fraco);font-size:11.5px;letter-spacing:.07em;text-transform:uppercase;
font-weight:700}
.metrica .val{font-size:32px;font-weight:800;letter-spacing:-.02em;margin-top:4px}
.metrica .pe{color:var(--fraco);font-size:12px}
.nota{color:var(--fraco);font-size:12.5px;margin:14px 0 0}
.barras{display:flex;align-items:flex-end;gap:6px;margin-top:10px}
.barras .col{flex:1;display:flex;flex-direction:column;justify-content:flex-end;
align-items:center;min-width:0}
.barras .bar{width:100%;background:linear-gradient(180deg,var(--teal),#1b7f74);
border-radius:6px 6px 0 0}
.barras span{display:block;text-align:center;font-size:10px;color:var(--fraco);margin-top:5px;
white-space:nowrap}
.cta{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;
background:linear-gradient(100deg,#19243c,#141d31);border:1px solid var(--borda);
border-radius:18px;padding:18px}
.cta p{margin:0;font-size:15px}
.cta a{background:linear-gradient(96deg,#2ee6c5,#4f8cff);color:#04121a;text-decoration:none;
font-weight:800;padding:12px 20px;border-radius:12px;font-size:15px;white-space:nowrap}
.cta a:hover{filter:brightness(1.08)}
.rodape{color:var(--fraco);font-size:12.5px;margin-top:26px;padding-top:16px;
border-top:1px solid var(--borda);display:flex;flex-wrap:wrap;gap:8px 18px}
.vazio{color:var(--fraco);font-size:14px}
.carregando{color:var(--fraco);padding:28px 0;text-align:center}
"""

JS = """
(function(){
  var alvoTopo = document.getElementById('topo');
  var alvoBaixo = document.getElementById('baixo');
  var selo = document.getElementById('atualizado');
  function troca(alvo, url){
    if(!alvo) return;
    fetch(url, {cache:'no-store'}).then(function(r){
      if(!r.ok) throw new Error('status '+r.status);
      return r.text();
    }).then(function(txt){ if(txt && txt.trim()) alvo.innerHTML = txt; }).catch(function(){});
  }
  function atualiza(){
    troca(alvoTopo, 'topo.html');
    troca(alvoBaixo, 'blocos.html');
    if(selo) selo.textContent = new Date().toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'});
  }
  setInterval(atualiza, 60000);
  document.addEventListener('visibilitychange', function(){ if(!document.hidden) atualiza(); });
})();
"""


# ------------------------------------------------------------------- pedaços
def _insights(dados: dict) -> str:
    itens = dados.get("insights") or []
    if not itens:
        return '<p class="vazio">Sem contas para mostrar neste momento.</p>'
    rotulos = {"dado": ("DADO", "t-dado"), "conta": ("CONTA", "t-conta"),
               "estimativa": ("ESTIMATIVA", "t-estimativa")}
    partes = []
    for item in itens:
        nome, classe = rotulos.get(item.get("tipo", "conta"), ("CONTA", "t-conta"))
        partes.append(
            f'<div class="insight"><span class="tag {classe}">{nome}</span>'
            f'<p>{esc(item.get("texto"))}</p></div>')
    return "".join(partes)


def _placar(dados: dict) -> str:
    nac = dados.get("nacional") or {}
    cores = cores_candidatos(nac)
    cands = (nac.get("candidatos") or [])[:2]
    if not cands:
        return '<p class="vazio">O TSE ainda não publicou candidatos para este turno.</p>'
    cartoes, barra, legenda = [], [], []
    for c in cands:
        cor = cores.get(c.get("nome"), CORES[0])
        cartoes.append(
            f'<div class="cand" style="--cor:{cor}">'
            f'<div class="nome"><span>{esc(c.get("nome"))}</span>'
            f'<em>{esc(c.get("partido"))}</em></div>'
            f'<div class="num">{pct(c.get("pct"), 2)}%</div>'
            f'<div class="votos">{milhar(c.get("votos"))} votos</div></div>')
        barra.append(f'<div style="width:{max(0.0, min(100.0, float(c.get("pct") or 0)))}%;'
                     f'background:{cor}"></div>')
        legenda.append(f'<span><b style="background:{cor}"></b>{esc(c.get("nome"))}</span>')
    return ("".join(cartoes)
            + f'<div class="barra">{"".join(barra)}</div>'
            + f'<div class="legenda">{"".join(legenda)}'
              f'<span>percentual sobre votos válidos</span></div>')


def _linha_regioes(dados: dict) -> str:
    regioes = dados.get("regioes") or []
    if not regioes:
        return '<p class="vazio">sem dado por região.</p>'
    nac = dados.get("nacional") or {}
    cores = cores_candidatos(nac)
    nomes = [c.get("nome") for c in (nac.get("candidatos") or [])[:2]]
    cab = ['<tr><th>Região</th><th class="dir">Apurado</th>']
    for nome in nomes:
        cab.append(f'<th class="dir">{esc(nome)}</th>')
    cab.append('<th class="dir">Peso no país</th></tr>')
    linhas = []
    for r in regioes:
        mapa = {c.get("nome"): c for c in r.get("candidatos") or []}
        cls = ' class="destaque"' if r.get("regiao") == "Nordeste" else ""
        celulas = [f'<td><b>{esc(r.get("regiao"))}</b>'
                   f'<div class="mini"><span style="width:'
                   f'{min(100.0, max(0.0, float(r.get("pct_apurado") or 0)))}%"></span></div></td>',
                   f'<td class="dir">{pct(r.get("pct_apurado"))}%</td>']
        for nome in nomes:
            c = mapa.get(nome) or {}
            cor = cores.get(nome, "#fff")
            celulas.append(f'<td class="dir" style="color:{cor}">{pct(c.get("pct"))}%</td>')
        celulas.append(f'<td class="dir">{pct(r.get("share_votos"))}%</td>')
        linhas.append(f'<tr{cls}>{"".join(celulas)}</tr>')
    return f'<table class="tabela">{"".join(cab)}{"".join(linhas)}</table>'


def _mapa(dados: dict) -> str:
    mapa = dados.get("mapa") or {}
    ufs = mapa.get("ufs") or {}
    if not ufs:
        return ""
    nac = dados.get("nacional") or {}
    cores = cores_candidatos(nac)
    por_uf = {u.get("uf"): u for u in dados.get("ufs") or []}
    partes = []
    for sigla, geo in ufs.items():
        info = por_uf.get(sigla) or {}
        lider = info.get("lider")
        cor = cores.get(lider, "#22304c")
        pct_ap = info.get("pct_apurado") or 0
        opacidade = 0.30 + 0.70 * min(1.0, pct_ap / 100.0)
        partes.append(
            f'<path d="{esc(geo.get("d"))}" fill="{cor}" fill-opacity="{opacidade:.2f}" '
            f'stroke="#0a1220" stroke-width="1.1"><title>{esc(sigla)} — '
            f'{esc(lider or "sem dado")} {pct(info.get("lider_pct"))}%</title></path>')
    return (f'<svg class="figura" viewBox="{esc(mapa.get("viewBox"))}" '
            f'xmlns="http://www.w3.org/2000/svg" role="img" '
            f'aria-label="Mapa do Brasil colorido pelo candidato que lidera em cada estado">'
            f'{"".join(partes)}</svg>')


def _estados(dados: dict) -> str:
    nac = dados.get("nacional") or {}
    cores = cores_candidatos(nac)
    blocos = []
    for u in dados.get("ufs") or []:
        cor = cores.get(u.get("lider"), "#22304c")
        blocos.append(
            f'<div class="uf" style="--cor:{cor}"><div class="sigla">{esc(u.get("uf"))}'
            f'<em>{pct(u.get("pct_apurado"), 0)}%</em></div>'
            f'<div class="lider">{esc(u.get("lider") or "sem dado")} '
            f'{pct(u.get("lider_pct"))}%</div>'
            f'<div class="faixa"><span style="width:{min(100.0, max(0.0, float(u.get("lider_pct") or 0)))}%"></span></div>'
            f'</div>')
    if not blocos:
        return '<p class="vazio">sem dado por estado.</p>'
    return f'<div class="grade-ufs">{"".join(blocos)}</div>'


def _linha_tempo(dados: dict) -> str:
    serie = dados.get("serie") or []
    if len(serie) < 2:
        return ('<p class="vazio">A linha do tempo aparece quando o TSE publica uma nova '
                'parcial: por enquanto existe 1 ponto (a fonte não mudou desde a última '
                'coleta). Nada é interpolado.</p>')
    nac = dados.get("nacional") or {}
    cores = cores_candidatos(nac)
    pontos = []
    for p in serie:
        marcas = []
        for c in p.get("candidatos") or []:
            marcas.append(f'{esc(c.get("nome"))} {pct(c.get("pct"), 2)}%')
        pontos.append(f'<li>{esc(p.get("coletado_em") or p.get("em"))} — '
                      f'{pct(p.get("pct_apurado"))}% apurado · {" · ".join(marcas)}</li>')
    return (f'<ul class="vazio" style="padding-left:18px">{"".join(pontos)}</ul>'
            f'<p class="nota">Cada ponto é uma leitura real do arquivo do TSE, gravada em '
            f'série append-only. Sem interpolação entre pontos.</p>')


def _metricas(m: dict) -> str:
    hoje, total = m.get("hoje") or {}, m.get("total") or {}
    estados = m.get("estados") or []
    serie = m.get("serie") or []
    maior = max([s.get("pessoas") or 0 for s in serie] + [1])

    def barras() -> str:
        if not serie:
            return '<p class="vazio">Ainda sem série diária.</p>'
        itens = []
        for s in serie:
            alt = max(4, int(58 * (s.get("pessoas") or 0) / maior))
            itens.append(
                f'<div class="col" title="{esc(s.get("dia"))}: '
                f'{s.get("pessoas")} pessoa(s), {s.get("visitas")} visita(s)">'
                f'<div class="bar" style="height:{alt}px"></div>'
                f'<span>{esc(s.get("rotulo"))}</span></div>')
        return f'<div class="barras">{"".join(itens)}</div>'

    def regioes() -> str:
        if not m.get("regioes"):
            return '<p class="vazio">Ainda sem região identificada.</p>'
        maior_r = max([r.get("pessoas") or 0 for r in m["regioes"]] + [1])
        itens = []
        for r in m["regioes"]:
            largura = int(100 * (r.get("pessoas") or 0) / maior_r)
            itens.append(f'<tr><td>{esc(r.get("regiao"))}</td>'
                         f'<td class="dir">{r.get("pessoas")}</td>'
                         f'<td class="dir">{r.get("visitas")}</td>'
                         f'<td style="width:38%"><div class="mini">'
                         f'<span style="width:{largura}%"></span></div></td></tr>')
        return ('<table class="tabela"><tr><th>Região (GeoIP)</th>'
                '<th class="dir">Pessoas</th><th class="dir">Visitas</th><th></th></tr>'
                f'{"".join(itens)}</table>')

    return f"""
    <div class="metricas">
      <div class="metrica"><div class="rot">Pessoas hoje</div>
        <div class="val">{milhar(hoje.get("pessoas") or 0)}</div>
        <div class="pe">{milhar(hoje.get("visitas") or 0)} visita(s)</div></div>
      <div class="metrica"><div class="rot">Pessoas no total</div>
        <div class="val">{milhar(total.get("pessoas") or 0)}</div>
        <div class="pe">desde {esc(total.get("desde") or "hoje")}</div></div>
      <div class="metrica"><div class="rot">Visitas hoje</div>
        <div class="val">{milhar(hoje.get("visitas") or 0)}</div>
        <div class="pe">ontem: {milhar((m.get("ontem") or {}).get("visitas") or 0)}</div></div>
      <div class="metrica"><div class="rot">Visitas no total</div>
        <div class="val">{milhar(total.get("visitas") or 0)}</div>
        <div class="pe">{milhar(m.get("bots_filtrados") or 0)} acesso(s) de robô filtrado(s)</div></div>
    </div>
    <h2 style="margin-top:22px">De onde vêm as visitas</h2>
    {regioes()}
    <h2 style="margin-top:22px">Pessoas por dia</h2>
    {barras()}
    <p class="nota"><b>O painel conta visitas de forma anônima.</b> {esc(m.get("anonimato"))}
    GeoIP: {esc(m.get("fonte_geo"))}. Retenção de {esc(m.get("retencao_dias"))} dias; nenhum
    IP é gravado e nada é cruzado com identidade.</p>
    """


def _cabecalho_bloco(dados: dict) -> str:
    nac = dados.get("nacional") or {}
    andamento = {"f": "apuração encerrada", "s": "apuração em andamento",
                 "n": "apuração não iniciada"}.get(dados.get("andamento"), "sem informação")
    partes = [f"""
    <div class="card">
      <h2>{esc(dados.get("turno"))} · TSE</h2>
      <div class="legenda" style="margin:0;gap:10px 22px">
        <span><b style="background:var(--teal)"></b>{esc(andamento)}</span>
        <span>{pct(nac.get("pct_apurado"))}% das seções apuradas
              ({milhar(nac.get("st"))} de {milhar(nac.get("ts"))})</span>
        <span>{milhar(nac.get("vv"))} votos válidos</span>
        <span>arquivo do TSE de {esc(dados.get("fonte_gerada_texto") or "—")}</span>
      </div>
    </div>"""]
    proximo = dados.get("proximo")
    if proximo:
        partes.append(
            f'<div class="aviso"><b>{esc(proximo.get("turno"))}:</b> '
            f'a apuração ainda não começou no TSE '
            f'({milhar(proximo.get("st"))} de {milhar(proximo.get("ts"))} seções). '
            f'O placar abaixo é o {esc(dados.get("turno"))}, já encerrado — '
            f'atualização do painel em {esc(dados.get("gerado_em", "")[:16].replace("T", " "))}.</div>')
    return "".join(partes)


def bloco_topo(dados: dict) -> str:
    """Topo dinâmico: cabeçalho da apuração, placar e insights. O mapa fica fixo no meio."""
    return f"""
    {_cabecalho_bloco(dados)}
    <div class="card"><h2>Placar</h2><div class="placar">{_placar(dados)}</div></div>
    """


def bloco_baixo(dados: dict, metricas: dict) -> str:
    """Resto: insights, regiões, estados, linha do tempo e o bloco de acesso."""
    return f"""
    <div class="card"><h2>O que os números dizem</h2>{_insights(dados)}</div>
    <div class="card"><h2>Por região</h2>{_linha_regioes(dados)}</div>
    <div class="card"><h2>Por estado</h2>{_estados(dados)}</div>
    <div class="card"><h2>Linha do tempo</h2>{_linha_tempo(dados)}</div>
    <div class="card"><h2>Acesso ao painel</h2>{_metricas(metricas)}</div>
    """


def shell(dados: dict | None, metricas: dict, cfg: dict) -> str:
    canal = (cfg.get("canal_url") or CANAL_YOUTUBE).strip() or CANAL_YOUTUBE
    rotulo = "Se inscreve no meu canal"
    mapa = _mapa(dados) if dados else ""
    if dados:
        topo = bloco_topo(dados)
        baixo = bloco_baixo(dados, metricas)
    else:
        topo = ('<div class="card"><div class="carregando">Coletando os números do TSE…'
                '<br><span class="vazio">a página recarrega sozinha em instantes.</span>'
                '</div></div>')
        baixo = ""
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(TITULO)}</title>
<meta name="description" content="Acompanhe a apuração da eleição presidencial de 2026 com dados oficiais do TSE, por estado e por região.">
<meta name="robots" content="index,follow">
<style>{CSS}</style>
</head>
<body>
<div class="envelope">
  <header class="topo">
    <span class="selo"><i></i>dados oficiais do TSE</span>
    <h1>Eleição 2026 <span>· Presidente</span></h1>
    <p class="sub">Apuração por estado e por região, sem chute: só o que o Tribunal
    Superior Eleitoral publica. Atualiza sozinho
    <span id="atualizado">agora</span>.</p>
  </header>

  <main id="topo">
    {topo}
  </main>

  {f'<div class="card"><h2>Mapa do momento</h2>{mapa}<p class="nota">Cor = candidato que lidera no estado. Opacidade = parte das seções já apuradas. Geometria pública; números do TSE.</p></div>' if mapa else ''}

  <main id="baixo">
    {baixo}
  </main>

  <div class="pilha">
    <div class="cta">
      <p><b>Gostou do painel?</b> Ele é atualizado direto da fonte oficial e eu explico
      os bastidores no canal.</p>
      <a href="{esc(canal)}" target="_blank" rel="noopener noreferrer"
         aria-label="Se inscreve no meu canal do YouTube (abre em nova aba)">{rotulo} →</a>
    </div>
    <div class="rodape">
      <span>Fonte: TSE · arquivos públicos de resultado por UF</span>
      <span>Nada é estimado sem estar marcado como estimativa</span>
      <span>O painel conta visitas de forma anônima</span>
    </div>
  </div>
</div>
<script>{JS}</script>
</body>
</html>"""


# ---------------------------------------------------------------------- HTTP
RE_IP = re.compile(r"^[0-9a-fA-F:.]{3,45}$")


def _ip_cliente(handler: BaseHTTPRequestHandler) -> str | None:
    """IP do visitante que o Cloudflare/túnel informa.

    Só vale porque este servidor escuta em 127.0.0.1: o único que fala com ele é o
    router, que repassa os cabeçalhos do Cloudflare. Exposição direta não existe.
    """
    for cabecalho in ("CF-Connecting-IP", "X-Real-IP"):
        valor = (handler.headers.get(cabecalho) or "").strip()
        if valor and RE_IP.match(valor):
            return valor
    encadeado = (handler.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    if encadeado and RE_IP.match(encadeado):
        return encadeado
    return handler.client_address[0] if handler.client_address else None


class Publico(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "painel-eleicao-publico"
    sys_version = ""

    # ------------------------------------------------------------- utilidades
    def log_message(self, format, *args):         # log enxuto e sem IP
        caminho = (self.path or "").split("?")[0]
        if caminho in ("/health",):
            return
        print("[%s] %s %s" % (self.log_date_time_string(), self.command, caminho), flush=True)

    def _manda(self, corpo: bytes, tipo: str, status: int = 200, cache: str = "no-store"):
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
            "img-src data:; connect-src 'self'; base-uri 'none'; form-action 'none'",
        )
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)
        self.close_connection = True

    def _html(self, texto: str, status: int = 200):
        self._manda(texto.encode("utf-8"), "text/html; charset=utf-8", status)

    def _json(self, dados, status: int = 200):
        corpo = json.dumps(dados, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._manda(corpo, "application/json; charset=utf-8", status)

    def _404(self):
        self._html('<!doctype html><meta charset="utf-8"><title>404</title>'
                   '<p style="font-family:sans-serif">nada aqui. '
                   '<a href="/">voltar ao painel</a></p>', 404)

    # ----------------------------------------------------------------- rotas
    def do_GET(self):
        caminho = (self.path or "/").split("?", 1)[0]
        if caminho in ("", "/"):
            return self._pagina()
        if caminho == "/topo.html":
            dados = retrato()
            if not dados:
                return self._manda(b"", "text/html; charset=utf-8")
            return self._html(bloco_topo(dados))
        if caminho == "/blocos.html":
            dados = retrato()
            if not dados:
                return self._manda(b"", "text/html; charset=utf-8")
            return self._html(bloco_baixo(dados, mp.resumo()))
        if caminho == "/dados.json":
            dados = retrato()
            if not dados:
                return self._json({"erro": "coleta em andamento"}, 503)
            return self._json(dados)
        if caminho == "/metricas.json":
            return self._json(mp.resumo())
        if caminho == "/health":
            return self._manda(b"ok", "text/plain; charset=utf-8")
        return self._404()

    do_HEAD = do_GET

    def do_POST(self):
        self._404()

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_POST

    def _pagina(self):
        try:
            if self.command != "HEAD":       # HEAD é monitoração: não infla a estatística
                mp.registra(_ip_cliente(self), self.headers.get("User-Agent"))
        except Exception as erro:                 # estatística nunca derruba a página
            print(f"  falha ao registrar visita: {type(erro).__name__}: {erro}", flush=True)
        try:
            metricas = mp.resumo()
        except Exception:
            metricas = {"hoje": {}, "total": {}, "regioes": [], "serie": [], "estados": []}
        self._html(shell(retrato(), metricas, config()))


# ------------------------------------------------------------- atualização
def mantenedor():
    """Regenera o retrato público em segundo plano, com intervalo adaptativo."""
    while True:
        espera = INTERVALO_PARADO
        try:
            dados = sp.montar(verboso=True)
            sp.grava(dados)
            _CACHE["erro"] = None
            if dados.get("andamento") == "s":
                espera = INTERVALO_VIVO
            print(f"  retrato atualizado · {dados.get('turno')} · "
                  f"{pct((dados.get('nacional') or {}).get('pct_apurado'))}% · "
                  f"próxima em {espera}s", flush=True)
        except Exception as erro:
            _CACHE["erro"] = f"{type(erro).__name__}: {erro}"
            print(f"  falha ao atualizar o retrato: {_CACHE['erro']}", flush=True)
            traceback.print_exc()
            espera = 300
        time.sleep(espera)


def main() -> int:
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        print(__doc__)
        return 0
    threading.Thread(target=mantenedor, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORTA), Publico)
    srv.daemon_threads = True
    print(f"painel eleitoral público em http://127.0.0.1:{PORTA} "
          f"(exposto pelo router em /eleicao, sem Basic Auth)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
