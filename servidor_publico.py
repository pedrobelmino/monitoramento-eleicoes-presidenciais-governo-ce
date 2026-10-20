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
from urllib.parse import parse_qs, urlparse

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

import fontes_ufs as fu              # noqa: E402
import metricas_publico as mp          # noqa: E402
import snapshot_publico as sp          # noqa: E402

PORTA = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", "8099"))
RETRATO = Path(os.environ.get("PAINEL_PUBLICO_JSON", AQUI / "dados" / "publico.json"))
SERIE_2022 = Path(os.environ.get(
    "PAINEL_SERIE_2022",
    Path.home() / ".hermes" / "eleicao_presidente" / "serie_historica_2022.jsonl",
))
# Cópia de trabalho dentro do repositório: útil quando o diretório do painel não
# é gravável (por exemplo, em ambiente de desenvolvimento com home somente leitura).
SERIE_2022_LOCAL = AQUI / "dados" / "serie_historica_2022.jsonl"
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
# Retratos por turno pedido (6257/6258). A coleta é cara (28 arquivos do TSE),
# então cada turno tem seu próprio cache com validade adaptativa.
_CACHE_EL: dict[str, dict] = {}
_LOCK_EL = threading.Lock()
_LOCK_COLETA = threading.Lock()
# Cache curto da eleição ativa: define o turno padrão do seletor.
_CACHE_ATIVA: dict[str, object] = {"quando": 0.0, "eleicao": "6257"}
_LOCK_ATIVA = threading.Lock()
# Série histórica de 2022 (JSONL grande): recarrega só quando o arquivo muda.
_CACHE_2022: dict[str, object] = {"mtime": 0.0, "dados": None}


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


def _validade(eleicao: str) -> int:
    """Turno em andamento merece cache curto; turno encerrado pode esperar."""
    return INTERVALO_VIVO if str(eleicao) == "6258" else INTERVALO_PARADO


def _cache_el_vivo(eleicao: str) -> dict | None:
    item = _CACHE_EL.get(str(eleicao))
    if not item:
        return None
    if time.time() - float(item.get("quando") or 0) >= _validade(eleicao):
        return None
    return item.get("dados") if isinstance(item.get("dados"), dict) else None


def retrato_eleicao(eleicao: str) -> dict:
    """Retrato de um turno específico, usando o ``_motor()`` do snapshot.

    Se o TSE não tiver publicado nada para o turno, devolve um retrato vazio com
    ``sem_dado=True`` — a tela diz "sem dado", nunca um placar de zeros.
    """
    eleicao = str(eleicao)
    pronto = _cache_el_vivo(eleicao)
    if pronto is not None:
        return pronto

    with _LOCK_COLETA:
        # Outra thread pode ter coletado enquanto esperávamos o lock.
        pronto = _cache_el_vivo(eleicao)
        if pronto is not None:
            return pronto
        try:
            dados = sp.montar_eleicao(eleicao, verboso=True)
        except Exception as erro:
            dados = sp.retrato_vazio(eleicao, f"{type(erro).__name__}: {erro}")
        _CACHE_EL[eleicao] = {"quando": time.time(), "dados": dados}
        return dados


def eleicao_padrao() -> str:
    """6258 quando o 2º turno já começou; senão 6257.

    Usa exatamente ``snapshot_publico.eleicao_ativa_local()`` (com cache curto
    para não consultar o TSE a cada visita).
    """
    agora = time.time()
    with _LOCK_ATIVA:
        if agora - float(_CACHE_ATIVA.get("quando") or 0) < 120:
            return str(_CACHE_ATIVA.get("eleicao") or "6257")
    try:
        ativa = sp.eleicao_ativa_local()
    except Exception:
        ativa = "6257"
    ativa = "6258" if str(ativa) == "6258" else "6257"
    with _LOCK_ATIVA:
        _CACHE_ATIVA["quando"], _CACHE_ATIVA["eleicao"] = agora, ativa
    return ativa


def serie_2022() -> list[dict]:
    """Lê o JSONL da referência de 2022 (deduplicado por ``em``)."""
    caminho = SERIE_2022 if SERIE_2022.exists() else SERIE_2022_LOCAL
    try:
        mtime = caminho.stat().st_mtime
    except OSError:
        return []
    if _CACHE_2022.get("dados") is not None and mtime == _CACHE_2022.get("mtime"):
        return _CACHE_2022["dados"]       # type: ignore[return-value]
    pontos, vistos = [], set()
    try:
        with caminho.open("r", encoding="utf-8") as arquivo:
            for linha in arquivo:
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    reg = json.loads(linha)
                except json.JSONDecodeError:
                    continue
                if not isinstance(reg, dict):
                    continue
                em = reg.get("em")
                if not em or em in vistos:
                    continue
                vistos.add(em)
                pontos.append(reg)
    except OSError:
        return []
    _CACHE_2022["mtime"], _CACHE_2022["dados"] = mtime, pontos
    return pontos


def _query(handler: BaseHTTPRequestHandler) -> dict:
    return parse_qs(urlparse(handler.path or "").query)


def _eleicao_pedida(handler: BaseHTTPRequestHandler) -> str:
    valores = _query(handler).get("eleicao") or [""]
    return (valores[0] or "").strip()


def _dados_para(handler: BaseHTTPRequestHandler) -> dict | None:
    eleicao = _eleicao_pedida(handler)
    if eleicao in ("6257", "6258"):
        return retrato_eleicao(eleicao)
    return retrato()


def metricas_resumo() -> dict:
    """Resumo anônimo; falha da estatística nunca derruba um bloco eleitoral."""
    try:
        return mp.resumo()
    except Exception:
        return {"hoje": {}, "total": {}, "regioes": [], "serie": [], "estados": []}


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
.turnos{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}
.turno,.camada{appearance:none;cursor:pointer;font:inherit;font-size:13px;font-weight:700;
color:var(--texto);background:#0b1424;border:1px solid var(--borda);border-radius:999px;
padding:8px 14px;transition:filter .15s,background .15s}
.turno:hover,.camada:hover{filter:brightness(1.18)}
.turno.ativo{background:linear-gradient(96deg,var(--teal),var(--azul));border-color:transparent;
color:#04121a}
.camada.ativa{background:#123a52;border-color:#2ee6c5;color:#bffcf2}
.selos{display:inline-flex;gap:5px;margin-left:8px;vertical-align:middle;flex-wrap:wrap}
.selos .tag{margin-top:0;font-size:9px;padding:2px 6px}
.barras-regiao{display:grid;gap:11px}
.barra-regiao .rot{display:flex;justify-content:space-between;gap:10px;font-size:13px;
align-items:baseline}
.barra-regiao .rot b{font-size:14px}
.barra-regiao .rot span{color:var(--fraco);font-size:12px;text-align:right}
.barra-regiao .trilho{height:11px;border-radius:999px;background:#0a1120;border:1px solid var(--borda);
overflow:hidden;margin-top:5px}
.barra-regiao .trilho span{display:block;height:100%;border-radius:999px;
background:linear-gradient(90deg,#1b7f74,var(--teal))}
.barra-regiao.destaque-nordeste{background:#0d2a2a;border:1px solid #1c6155;border-radius:12px;
padding:10px 11px;margin:0 -5px}
.barra-regiao.destaque-nordeste .rot b{color:var(--teal)}
.fenomeno{display:grid;gap:16px}
.fenomeno .conta{background:#0b1424;border:1px solid var(--borda);border-radius:14px;
padding:14px}
.fenomeno .conta p{margin:0 0 10px;font-size:14.5px}
.fenomeno .conta p:last-child{margin-bottom:0}
.fenomeno .conta .tag{margin-right:6px}
.serie-palco{min-height:180px}
.grafico-serie{width:100%;height:auto;display:block;background:#0a1120;border:1px solid var(--borda);
border-radius:14px}
.serie-legenda{display:flex;gap:16px;flex-wrap:wrap;color:var(--fraco);font-size:13px;
margin:0 0 10px}
.serie-legenda b{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}
.ponto-unico{background:#0b1424;border:1px solid var(--borda);border-radius:14px;padding:16px}
.ponto-unico .rot{color:var(--fraco);font-size:12px;letter-spacing:.06em;text-transform:uppercase;
font-weight:700}
.ponto-unico .val{font-size:34px;font-weight:800;letter-spacing:-.02em;margin:4px 0 6px;
color:var(--teal)}
.lista-serie{padding-left:18px;margin:10px 0 0;color:var(--fraco);font-size:13px}
.lista-serie li{margin:5px 0}
details{margin-top:10px}
details summary{cursor:pointer;color:var(--teal);font-size:13px;font-weight:700}
.tabela a{color:var(--teal);text-decoration:none}
.tabela a:hover{text-decoration:underline}
.fontes-uf .nome{color:var(--fraco);font-size:11.5px}
.rodape a{color:var(--teal)}
"""

JS = r"""
(function(){
  var alvoTopo = document.getElementById('topo');
  var alvoMeio = document.getElementById('meio');
  var alvoBaixo = document.getElementById('baixo');
  var selo = document.getElementById('atualizado');
  var eleicaoAtual = document.documentElement.getAttribute('data-eleicao') || '6257';

  function url(base){ return base + '?eleicao=' + encodeURIComponent(eleicaoAtual); }
  function troca(alvo, endereco){
    if(!alvo) return;
    fetch(endereco, {cache:'no-store'}).then(function(r){
      if(!r.ok) throw new Error('status '+r.status);
      return r.text();
    }).then(function(txt){
      if(txt && txt.trim()) alvo.innerHTML = txt;
      if(alvo === alvoBaixo && window.PainelSerie) window.PainelSerie.monta();
    }).catch(function(){});
  }
  function marcaTurno(){
    var botoes = document.querySelectorAll('.turno');
    for(var i=0;i<botoes.length;i++){
      var b = botoes[i];
      var ativo = b.getAttribute('data-eleicao') === eleicaoAtual;
      b.className = 'turno' + (ativo ? ' ativo' : '');
      b.setAttribute('aria-pressed', ativo ? 'true' : 'false');
    }
  }
  function seleciona(nova){
    if(!nova || nova === eleicaoAtual) return;
    eleicaoAtual = nova;
    marcaTurno();
    atualiza();
  }
  function atualiza(){
    troca(alvoTopo, url('topo.html'));
    troca(alvoMeio, url('meio.html'));
    troca(alvoBaixo, url('blocos.html'));
    if(selo) selo.textContent = new Date().toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'});
  }
  document.addEventListener('click', function(ev){
    var alvo = ev.target;
    while(alvo && alvo !== document){
      if(alvo.classList && alvo.classList.contains('turno')){
        ev.preventDefault();
        seleciona(alvo.getAttribute('data-eleicao'));
        return;
      }
      alvo = alvo.parentNode;
    }
  });

  window.PainelSerie = (function(){
    var camada = '2026';
    try { camada = sessionStorage.getItem('painel-camada-serie') || '2026'; } catch(e){}
    var cache2022 = null;
    var buscando = false;

    function esc(t){
      return String(t==null?'':t).replace(/&/g,'&amp;').replace(/</g,'&lt;')
        .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }
    function cfg(){
      var el = document.getElementById('serie-config');
      if(!el) return null;
      try { return JSON.parse(el.textContent); } catch(e){ return null; }
    }
    function guarda(){ try { sessionStorage.setItem('painel-camada-serie', camada); } catch(e){} }
    function pintaBotoes(){
      var botoes = document.querySelectorAll('.camada');
      for(var i=0;i<botoes.length;i++){
        var b = botoes[i];
        var ativo = b.getAttribute('data-camada') === camada;
        b.className = 'camada' + (ativo ? ' ativa' : '');
        b.setAttribute('aria-pressed', ativo ? 'true' : 'false');
      }
    }
    function fmt(v,c){
      if(v===null || v===undefined || isNaN(v)) return '—';
      return Number(v).toFixed(c==null?2:c).replace('.',',');
    }
    function origemDe(p){ return (p && p.origem) || 'primeira_coleta'; }
    function resumoOrigem(pontos){
      var mapa = {};
      for(var i=0;i<pontos.length;i++){ var o=origemDe(pontos[i]); mapa[o]=(mapa[o]||0)+1; }
      var partes=[];
      for(var k in mapa){ if(mapa.hasOwnProperty(k)) partes.push(k+' ('+mapa[k]+')'); }
      return partes.join(' · ');
    }
    function validos(pontos){
      return (pontos||[]).filter(function(p){
        return p && p.pct_apurado !== null && p.pct_apurado !== undefined && !isNaN(p.pct_apurado);
      });
    }
    function desenha(pontos, c, rotulo){
      var palco = document.getElementById('serie-palco');
      if(!palco) return;
      var ps = validos(pontos);
      if(!ps.length){
        palco.innerHTML = '<p class="vazio">sem dado da série para '+esc(rotulo)+'.</p>';
        return;
      }
      if(ps.length === 1){
        var p = ps[0];
        var cands = (p.candidatos||[]).map(function(x){ return esc(x.nome)+' '+fmt(x.pct)+'%'; }).join(' · ');
        palco.innerHTML = '<div class="ponto-unico"><div class="rot">série começa em '+
          esc(p.em || (c && c.fonte) || '—')+'</div><div class="val">'+fmt(p.pct_apurado)+
          '% apurado</div><p class="nota">'+esc(cands || 'sem candidatos publicados')+
          ' · origem: <b>'+esc(origemDe(p))+'</b></p><p class="nota">Existe 1 ponto real. '+
          'A curva ganha corpo quando o TSE publicar uma nova parcial; nada é interpolado.</p></div>';
        return;
      }

      var nomes = [];
      if(rotulo !== '2022' && c && c.nomes) nomes = c.nomes.slice(0,2);
      if(!nomes.length){
        for(var i=0;i<ps.length;i++){
          var cs=ps[i].candidatos||[];
          for(var j=0;j<cs.length;j++){ if(nomes.indexOf(cs[j].nome)<0) nomes.push(cs[j].nome); }
        }
        nomes=nomes.slice(0,2);
      }
      var mapaCores = (c && c.cores) || {};
      if(rotulo === '2022') mapaCores = {'LULA':'#ff5a67','JAIR BOLSONARO':'#4f8cff'};
      function cor(nome, indice){
        if(mapaCores[nome]) return mapaCores[nome];
        var paleta = ['#4f8cff','#ff5a67','#f5c542','#3ddc97'];
        return paleta[indice % paleta.length];
      }

      var maxY = 0;
      for(var i=0;i<ps.length;i++){
        var cs=ps[i].candidatos||[];
        for(var j=0;j<cs.length;j++){
          if(cs[j].pct!==null && cs[j].pct!==undefined) maxY=Math.max(maxY, Number(cs[j].pct));
        }
      }
      maxY = Math.min(100, Math.max(50, Math.ceil((maxY+3)/10)*10));
      var W=720,H=300,L=46,R=16,T=18,B=38;
      var w=W-L-R, h=H-T-B;
      function x(v){ return L + w * Math.max(0,Math.min(100,Number(v)))/100; }
      function y(v){ return T + h - h * Math.max(0,Math.min(maxY,Number(v)))/maxY; }
      var svg=[];
      svg.push('<svg class="grafico-serie" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Curva da apuração por candidato">');
      for(var g=0;g<=4;g++){
        var yy=T+h*g/4, val=maxY*(1-g/4);
        svg.push('<line x1="'+L+'" y1="'+yy.toFixed(1)+'" x2="'+(L+w)+'" y2="'+yy.toFixed(1)+'" stroke="#1f2b45" stroke-width="1"/>');
        svg.push('<text x="'+(L-8)+'" y="'+(yy+4).toFixed(1)+'" fill="#93a3c4" font-size="11" text-anchor="end">'+fmt(val,0)+'%</text>');
      }
      for(var gx=0;gx<=4;gx++){
        var xx=L+w*gx/4;
        svg.push('<line x1="'+xx.toFixed(1)+'" y1="'+T+'" x2="'+xx.toFixed(1)+'" y2="'+(T+h)+'" stroke="#162034" stroke-width="1"/>');
        svg.push('<text x="'+xx.toFixed(1)+'" y="'+(T+h+20)+'" fill="#93a3c4" font-size="11" text-anchor="middle">'+(gx*25)+'%</text>');
      }
      svg.push('<text x="'+(L+w/2)+'" y="'+(H-4)+'" fill="#93a3c4" font-size="11" text-anchor="middle">seções apuradas (%)</text>');
      svg.push('<text x="12" y="'+(T+h/2)+'" fill="#93a3c4" font-size="11" text-anchor="middle" transform="rotate(-90 12 '+(T+h/2)+')">votos válidos (%)</text>');

      for(var ci=0;ci<nomes.length;ci++){
        var nome=nomes[ci], corr=cor(nome,ci), d='', n=0, ultimo=null;
        for(var i=0;i<ps.length;i++){
          var cs=ps[i].candidatos||[], achou=null;
          for(var j=0;j<cs.length;j++){ if(cs[j].nome===nome){ achou=cs[j]; break; } }
          if(!achou || achou.pct===null || achou.pct===undefined) continue;
          var px=x(ps[i].pct_apurado), py=y(achou.pct);
          d += (n===0?'M':'L') + px.toFixed(2)+' '+py.toFixed(2)+' ';
          n++; ultimo={x:px,y:py};
        }
        if(!n) continue;
        svg.push('<path d="'+d.trim()+'" fill="none" stroke="'+corr+'" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>');
        var passo=Math.max(1,Math.floor(ps.length/14));
        for(var i=0;i<ps.length;i+=passo){
          var cs=ps[i].candidatos||[], achou=null;
          for(var j=0;j<cs.length;j++){ if(cs[j].nome===nome){ achou=cs[j]; break; } }
          if(!achou || achou.pct===null || achou.pct===undefined) continue;
          var px=x(ps[i].pct_apurado), py=y(achou.pct);
          svg.push('<circle cx="'+px.toFixed(1)+'" cy="'+py.toFixed(1)+'" r="2.3" fill="'+corr+'"><title>'+
            esc(ps[i].em||'—')+' · '+esc(nome)+' '+fmt(achou.pct)+'% · apurado '+fmt(ps[i].pct_apurado)+
            '% · origem '+esc(origemDe(ps[i]))+'</title></circle>');
        }
        if(ultimo) svg.push('<text x="'+Math.min(W-4,ultimo.x+6).toFixed(1)+'" y="'+(ultimo.y-6).toFixed(1)+'" fill="'+corr+'" font-size="11" font-weight="700">'+esc(nome)+'</text>');
      }
      svg.push('</svg>');
      var legend=[];
      for(var ci=0;ci<nomes.length;ci++){
        legend.push('<span><b style="background:'+cor(nomes[ci],ci)+'"></b>'+esc(nomes[ci])+'</span>');
      }
      var detalhes=[], ini=Math.max(0,ps.length-8);
      for(var i=ini;i<ps.length;i++){
        var p=ps[i], cs=p.candidatos||[], partes=[];
        for(var j=0;j<cs.length;j++){
          if(cs[j].pct!==null && cs[j].pct!==undefined) partes.push(esc(cs[j].nome)+' '+fmt(cs[j].pct)+'%');
        }
        detalhes.push('<li>'+esc(p.em||'—')+' — '+fmt(p.pct_apurado)+'% apurado · '+
          (partes.join(' · ')||'sem candidatos')+' · <b>'+esc(origemDe(p))+'</b></li>');
      }
      palco.innerHTML =
        '<div class="serie-legenda">'+legend.join('')+'</div>'+
        svg.join('')+
        '<p class="nota"><b>'+ps.length+'</b> ponto(s) reais de <b>'+esc(ps[0].em||'—')+
        '</b> a <b>'+esc(ps[ps.length-1].em||'—')+'</b>. Origem: '+esc(resumoOrigem(ps))+
        '. Sem interpolação entre pontos.</p>'+
        '<details><summary>Últimos pontos e a origem de cada um</summary><ul class="lista-serie">'+
        detalhes.join('')+'</ul></details>';
    }
    function busca2022(c){
      var palco=document.getElementById('serie-palco');
      if(cache2022){ desenha(cache2022, c, '2022'); return; }
      if(buscando) return;
      buscando=true;
      if(palco) palco.innerHTML='<p class="vazio">carregando a série histórica de 2022…</p>';
      fetch('serie.json?eleicao=2022', {cache:'no-store'}).then(function(r){
        if(!r.ok) throw new Error('status '+r.status);
        return r.json();
      }).then(function(d){
        cache2022 = (d && d.pontos) || [];
        buscando=false;
        desenha(cache2022, c, '2022');
      }).catch(function(){
        buscando=false;
        if(palco) palco.innerHTML='<p class="vazio">não foi possível carregar a série de 2022 agora.</p>';
      });
    }
    function render(){
      var c=cfg();
      if(!c){
        var p=document.getElementById('serie-palco');
        if(p) p.innerHTML='<p class="vazio">sem dado da série.</p>';
        return;
      }
      if(camada==='2022') busca2022(c);
      else desenha(c.serie||[], c, '2026');
    }
    function monta(){
      var palco=document.getElementById('serie-palco');
      if(!palco) return;
      var botoes=document.querySelectorAll('.camada');
      for(var i=0;i<botoes.length;i++){
        botoes[i].onclick=function(){
          camada=this.getAttribute('data-camada')||'2026';
          guarda(); pintaBotoes(); render();
        };
      }
      pintaBotoes();
      render();
    }
    return {monta:monta};
  })();

  marcaTurno();
  if(window.PainelSerie) window.PainelSerie.monta();
  var servido = alvoTopo && alvoTopo.getAttribute('data-eleicao');
  if(!servido || servido !== eleicaoAtual) atualiza();
  setInterval(atualiza, 60000);
  document.addEventListener('visibilitychange', function(){ if(!document.hidden) atualiza(); });
})();
"""


# ------------------------------------------------------------------- pedaços
def _selos(*tipos: str) -> str:
    """Selos de procedência do bloco: TSE, CONTA e ESTIMATIVA."""
    rotulos = {"TSE": ("TSE", "t-dado"), "CONTA": ("CONTA", "t-conta"),
               "ESTIMATIVA": ("ESTIMATIVA", "t-estimativa")}
    partes = []
    for tipo in tipos:
        nome, classe = rotulos.get(tipo, (tipo, "t-conta"))
        partes.append(f'<span class="tag {classe}">{nome}</span>')
    return f'<span class="selos">{"".join(partes)}</span>'


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


def _fenomeno(dados: dict) -> str:
    """Painel do fenômeno: avanço por região + conta do que falta para virar.

    Tudo sai dos números publicados: as barras são o % de seções apuradas por
    região; a projeção de votos restantes é uma ESTIMATIVA com a premissa
    escrita ao lado. Quando não falta seção, a conta não se aplica e o bloco
    mostra o resultado oficial, sem inventar projeção.
    """
    nac = dados.get("nacional") or {}
    candidatos = nac.get("candidatos") or []
    regioes = dados.get("regioes") or []
    ufs = dados.get("ufs") or []
    if not candidatos:
        return '<p class="vazio">sem dado para o painel do fenômeno neste turno.</p>'

    primeiro = candidatos[0]
    segundo = candidatos[1] if len(candidatos) > 1 else None

    # 1) Regiões: ordem de entrada = % de seções já apuradas (maior primeiro).
    com_pct = [r for r in regioes if r.get("pct_apurado") is not None]
    com_pct.sort(key=lambda r: -float(r.get("pct_apurado") or 0))
    barras = []
    for r in com_pct:
        nome_regiao = r.get("regiao") or "?"
        p = max(0.0, min(100.0, float(r.get("pct_apurado") or 0)))
        faltam = r.get("secoes_faltantes")
        if faltam is None:
            falta_txt = "seções faltantes sem dado"
        elif faltam:
            falta_txt = f"faltam {milhar(faltam)} seções"
        else:
            falta_txt = "sem seções faltantes"
        destaque = " destaque-nordeste" if nome_regiao == "Nordeste" else ""
        barras.append(
            f'<div class="barra-regiao{destaque}">'
            f'<div class="rot"><b>{esc(nome_regiao)}</b>'
            f'<span>{pct(p)}% apurado · {esc(falta_txt)}</span></div>'
            f'<div class="trilho"><span style="width:{p:.2f}%"></span></div></div>')
    bloco_barras = (
        f'<div class="barras-regiao">{"".join(barras)}</div>'
        f'<p class="nota">Ordem de entrada das regiões pelo percentual de seções '
        f'já apuradas. O <b>Nordeste</b> fica em destaque.</p>'
        if barras else '<p class="vazio">sem dado por região.</p>')

    # 2) Votos válidos restantes, UF por UF: (vv/st) × seções que faltam.
    por_uf = []
    for u in ufs:
        st, ts, vv = u.get("st"), u.get("ts"), u.get("vv")
        if st is None or ts is None or vv is None or int(st) <= 0:
            continue
        faltam = max(0, int(ts) - int(st))
        media = float(vv) / float(st)
        por_uf.append({
            "uf": u.get("uf"), "faltam": faltam, "media": media,
            "restante": media * faltam,
        })
    por_uf.sort(key=lambda x: (-x["restante"], str(x["uf"] or "")))
    restante_total = sum(x["restante"] for x in por_uf)

    if por_uf:
        faltam_total = sum(x["faltam"] for x in por_uf)
    else:
        ts, st = nac.get("ts"), nac.get("st")
        faltam_total = (max(0, int(ts) - int(st))
                        if ts is not None and st is not None else None)

    def resultado_oficial() -> str:
        partes = [
            f'{esc(primeiro.get("nome"))} ({esc(primeiro.get("partido"))}) '
            f'{milhar(primeiro.get("votos"))} votos ({pct(primeiro.get("pct"), 2)}%)'
        ]
        if segundo:
            partes.append(
                f'{esc(segundo.get("nome"))} ({esc(segundo.get("partido"))}) '
                f'{milhar(segundo.get("votos"))} votos ({pct(segundo.get("pct"), 2)}%)')
        return " · ".join(partes)

    conta = []
    if faltam_total == 0:
        conta.append(
            f'<p><span class="tag t-dado">TSE</span> A conta de votos restantes '
            f'<b>não se aplica</b>: o TSE já apurou 100% das seções. '
            f'Resultado oficial: {resultado_oficial()}.</p>')
    elif faltam_total is None:
        conta.append(
            '<p><span class="tag t-dado">TSE</span> Sem dado de seções faltantes '
            'para este turno; não há conta a fazer.</p>')
    elif not segundo:
        conta.append(
            '<p><span class="tag t-dado">TSE</span> Só há um candidato com votos '
            'publicados; não existe 2º colocado para comparar.</p>')
    elif primeiro.get("votos") is None or segundo.get("votos") is None:
        conta.append(
            '<p><span class="tag t-dado">TSE</span> Votos de 1º ou 2º ainda não '
            'publicados; a conta fica para a próxima parcial.</p>')
    else:
        diff = int(primeiro.get("votos") or 0) - int(segundo.get("votos") or 0)
        pp = float(primeiro.get("pct") or 0) - float(segundo.get("pct") or 0)
        conta.append(
            f'<p><span class="tag t-conta">CONTA</span> '
            f'{esc(primeiro.get("nome"))} está <b>{milhar(abs(diff))} votos</b> '
            f'({pct(abs(pp), 2)} ponto(s)) à frente de '
            f'{esc(segundo.get("nome"))}.</p>')
        if restante_total > 0:
            necessario = 100.0 * diff / restante_total
            if necessario <= 100:
                frase = (f'O 2º colocado precisaria de <b>{pct(necessario)}%</b> '
                         f'dos {milhar(int(round(restante_total)))} votos válidos '
                         f'restantes estimados para empatar.')
            else:
                frase = (f'O 2º colocado precisaria de <b>{pct(necessario)}%</b> '
                         f'dos {milhar(int(round(restante_total)))} votos válidos '
                         f'restantes estimados — mais de 100%, então nem todos os '
                         f'votos restantes bastariam pela premissa.')
            conta.append(f'<p><span class="tag t-estimativa">ESTIMATIVA</span> {frase}</p>')
            conta.append(
                '<p class="nota"><b>Premissa da estimativa:</b> os votos válidos '
                'restantes de cada UF são a média de votos válidos por seção já '
                'apurada (vv ÷ st) multiplicada pelas seções que faltam. O 1º '
                'colocado não recebe nenhum desses votos; é o mínimo que o 2º '
                'precisa para empatar. Projeção, não resultado.</p>')
        else:
            conta.append(
                '<p><span class="tag t-estimativa">ESTIMATIVA</span> Sem base para '
                'estimar votos restantes: nenhuma UF tem seção apurada com votos '
                'válidos publicados.</p>')

    tabela = ""
    if por_uf and faltam_total:
        linhas = []
        for x in por_uf:
            linhas.append(
                f'<tr><td><b>{esc(x["uf"])}</b></td>'
                f'<td class="dir">{milhar(x["faltam"])}</td>'
                f'<td class="dir">{pct(x["media"])}</td>'
                f'<td class="dir">{milhar(int(round(x["restante"])))}</td></tr>')
        tabela = (
            '<details><summary>Ver a conta por UF ('
            f'{len(por_uf)} UFs)</summary>'
            '<table class="tabela"><tr><th>UF</th><th class="dir">Seções faltando</th>'
            '<th class="dir">Votos válidos por seção</th>'
            '<th class="dir">Válidos restantes estimados</th></tr>'
            f'{"".join(linhas)}</table>'
            '<p class="nota">A soma das UFs é a base da estimativa; UF sem seção '
            'apurada fica fora da conta.</p></details>')

    return (f'<div class="fenomeno">{bloco_barras}'
            f'<div class="conta">{"".join(conta)}{tabela}</div></div>')


def _mapa(dados: dict) -> str:
    if dados.get("sem_dado"):
        return ""
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


def _fontes(dados: dict) -> str:
    """As 27 UFs com o site do TRE e o JSON público do TSE daquele estado."""
    eleicao = str(dados.get("eleicao") or "6257")
    linhas = dados.get("fontes_ufs") or []
    # Garante que os links são do turno que está na tela, não de um retrato antigo.
    if (len(linhas) != 27
            or any(f"/{eleicao}/" not in str(linha.get("json") or "") for linha in linhas)):
        try:
            linhas = fu.fontes(eleicao)
        except Exception:
            linhas = []
    if not linhas:
        return '<p class="vazio">sem dado das fontes por estado.</p>'
    corpo = []
    for linha in linhas:
        corpo.append(
            f'<tr><td><b>{esc(linha.get("uf"))}</b>'
            f'<div class="nome">{esc(linha.get("nome"))}</div></td>'
            f'<td>{esc(linha.get("regiao"))}</td>'
            f'<td><a href="{esc(linha.get("tre"))}" target="_blank" '
            f'rel="noopener noreferrer">TRE</a></td>'
            f'<td><a href="{esc(linha.get("json"))}" target="_blank" '
            f'rel="noopener noreferrer">JSON do TSE</a></td></tr>')
    return (
        '<table class="tabela fontes-uf"><tr><th>UF</th><th>Região</th>'
        '<th>Fonte estadual</th><th>Dado oficial</th></tr>'
        f'{"".join(corpo)}</table>'
        f'<p class="nota">Os links abrem em nova aba. Portal de resultados do TSE: '
        f'<a href="{esc(fu.PORTAL_TSE)}" target="_blank" rel="noopener noreferrer">'
        f'resultados.tse.jus.br</a>.</p>')


def _linha_tempo(dados: dict) -> str:
    """Linha do tempo com duas camadas: série de 2026 e referência de 2022."""
    nac = dados.get("nacional") or {}
    cores = cores_candidatos(nac)
    nomes = [c.get("nome") for c in (nac.get("candidatos") or [])[:2] if c.get("nome")]
    config = {
        "eleicao": dados.get("eleicao"),
        "turno": dados.get("turno"),
        "fonte": dados.get("fonte_gerada_texto"),
        "nomes": nomes,
        "cores": cores,
        "serie": dados.get("serie") or [],
    }
    # json.dumps evita quebrar o <script> e preserva nomes com acento.
    payload = json.dumps(config, ensure_ascii=False).replace("</", "<\\/")
    serie = dados.get("serie") or []
    if not serie:
        fallback = ('<p class="vazio">A série de 2026 ainda não tem ponto: '
                    'sem dado da fonte.</p>')
    elif len(serie) == 1:
        fallback = (
            f'<p class="vazio">A série de 2026 começa em '
            f'<b>{esc(serie[0].get("em") or dados.get("fonte_gerada_texto") or "—")}</b> '
            f'(origem: {esc(serie[0].get("origem") or "primeira_coleta")}). '
            f'Existe 1 ponto real; nada é interpolado.</p>')
    else:
        fallback = (f'<p class="vazio">{len(serie)} ponto(s) reais na série de 2026. '
                    f'Abra a camada de 2022 para comparar com a referência do TSE.</p>')
    return (
        '<div class="camadas" role="group" aria-label="Camada da linha do tempo">'
        '<button type="button" class="camada" data-camada="2026">'
        '2026 (ao vivo, série do painel)</button>'
        '<button type="button" class="camada" data-camada="2022">'
        '2022 (referência TSE, 1º turno)</button></div>'
        f'<div id="serie-palco" class="serie-palco">{fallback}</div>'
        f'<script type="application/json" id="serie-config">{payload}</script>'
        '<noscript><p class="vazio">Ative o JavaScript para ver as curvas; '
        'os pontos continuam disponíveis em <code>/serie.json?eleicao=2022</code>.'
        '</p></noscript>')


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
    """Topo dinâmico: cabeçalho da apuração, placar e insights."""
    return f"""
    {_cabecalho_bloco(dados)}
    <div class="card"><h2>Placar {_selos("TSE")}</h2><div class="placar">{_placar(dados)}</div></div>
    """


def bloco_meio(dados: dict) -> str:
    """Meio dinâmico: regiões, painel do fenômeno e mapa, nesta ordem."""
    mapa = _mapa(dados)
    mapa_html = ""
    if mapa:
        mapa_html = (
            f'<div class="card"><h2>Mapa do momento {_selos("TSE")}</h2>{mapa}'
            '<p class="nota">Cor = candidato que lidera no estado. Opacidade = parte '
            'das seções já apuradas. Geometria pública; números do TSE.</p></div>')
    return f"""
    <div class="card"><h2>Por região {_selos("TSE", "CONTA")}</h2>{_linha_regioes(dados)}</div>
    <div class="card"><h2>Painel do fenômeno {_selos("TSE", "CONTA", "ESTIMATIVA")}</h2>{_fenomeno(dados)}</div>
    {mapa_html}
    """


def bloco_baixo(dados: dict, metricas: dict) -> str:
    """Resto: insights, estados, fontes por UF, linha do tempo e acesso."""
    return f"""
    <div class="card"><h2>O que os números dizem {_selos("TSE", "CONTA", "ESTIMATIVA")}</h2>{_insights(dados)}</div>
    <div class="card"><h2>Por estado {_selos("TSE")}</h2>{_estados(dados)}</div>
    <div class="card"><h2>Fontes por estado {_selos("TSE")}</h2>{_fontes(dados)}</div>
    <div class="card"><h2>Linha do tempo {_selos("TSE", "CONTA")}</h2>{_linha_tempo(dados)}</div>
    <div class="card"><h2>Acesso ao painel {_selos("CONTA")}</h2>{_metricas(metricas)}</div>
    """


def shell(dados: dict | None, metricas: dict, cfg: dict, padrao: str = "6257") -> str:
    canal = (cfg.get("canal_url") or CANAL_YOUTUBE).strip() or CANAL_YOUTUBE
    rotulo = "Se inscreve no meu canal"
    if dados:
        topo = bloco_topo(dados)
        meio = bloco_meio(dados)
        baixo = bloco_baixo(dados, metricas)
    else:
        topo = ('<div class="card"><div class="carregando">Coletando os números do TSE…'
                '<br><span class="vazio">a página recarrega sozinha em instantes.</span>'
                '</div></div>')
        meio = ""
        baixo = ""
    servido = esc((dados or {}).get("eleicao") or "")
    ativo1 = " ativo" if str(padrao) == "6257" else ""
    ativo2 = " ativo" if str(padrao) == "6258" else ""
    return f"""<!doctype html>
<html lang="pt-BR" data-eleicao="{esc(padrao)}">
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
    <div class="turnos" role="group" aria-label="Turno">
      <button type="button" class="turno{ativo1}" data-eleicao="6257"
              aria-pressed="{'true' if str(padrao) == '6257' else 'false'}">1º turno</button>
      <button type="button" class="turno{ativo2}" data-eleicao="6258"
              aria-pressed="{'true' if str(padrao) == '6258' else 'false'}">2º turno</button>
    </div>
  </header>

  <main id="topo" data-eleicao="{servido}">
    {topo}
  </main>

  <main id="meio">
    {meio}
  </main>

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
        caminho = urlparse(self.path or "/").path or "/"
        if caminho in ("", "/"):
            return self._pagina()
        if caminho in ("/topo.html", "/meio.html", "/blocos.html"):
            dados = _dados_para(self)
            if not dados:
                return self._manda(b"", "text/html; charset=utf-8")
            if caminho == "/topo.html":
                return self._html(bloco_topo(dados))
            if caminho == "/meio.html":
                return self._html(bloco_meio(dados))
            return self._html(bloco_baixo(dados, mp.resumo()))
        if caminho == "/dados.json":
            eleicao = _eleicao_pedida(self)
            if eleicao in ("6257", "6258"):
                return self._json(retrato_eleicao(eleicao))
            dados = retrato()
            if not dados:
                return self._json({"erro": "coleta em andamento"}, 503)
            return self._json(dados)
        if caminho == "/serie.json":
            eleicao = _eleicao_pedida(self) or "2026"
            if eleicao == "2022":
                return self._json({
                    "eleicao": "2022",
                    "turno": "1º turno (referência)",
                    "origem": "arquivo_historico_tse",
                    "fonte": "TSE - Histórico Totalização Presidente 1º Turno 2022",
                    "pontos": serie_2022(),
                })
            dados = retrato() or _cache_el_vivo("6257") or {}
            return self._json({
                "eleicao": dados.get("eleicao") or eleicao,
                "turno": dados.get("turno"),
                "origem": "primeira_coleta",
                "fonte": dados.get("fonte_gerada_texto"),
                "pontos": dados.get("serie") or [],
            })
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
        padrao = eleicao_padrao()
        dados = retrato()
        # Se o retrato em disco ainda é do outro turno, não mostre o placar
        # errado sob o botão selecionado: o JS busca o turno padrão na hora.
        if str((dados or {}).get("eleicao") or "") != padrao:
            dados = _cache_el_vivo(padrao)
        self._html(shell(dados, metricas, config(), padrao))


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
