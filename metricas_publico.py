#!/usr/bin/env python3
"""Estatística de acesso do painel público — contagem anônima de visitas.

Pedido do Pedro (20/10/2026): "quantas pessoas visualizaram e de quais regiões",
sem cookies de terceiros e sem invadir ninguém.

Como a contagem é feita (e por que é anônima):

  * NÃO existe cookie, fingerprint, localStorage nem pixel de terceiro.
  * NÃO se guarda o IP. No instante da visita calcula-se

        hash = sha256( sal + dia + ip )[:32]

    e só o hash entra no banco. O sal é um segredo local (``.salt``, 0600) e o DIA
    entra no hash de propósito: o mesmo visitante tem um hash diferente amanhã,
    então não é possível emendar a trajetória de uma pessoa entre dias.
  * Do IP se extrai apenas o que o GeoIP devolve — país / estado / cidade — e
    nada mais. IP privado/loopback (127.0.0.1, 10.x, ...) não é geolocalizado:
    fica como "rede local".
  * User-agent vai RESUMIDO (navegador + sistema, ex.: "Chrome · Linux"), nunca
    a string inteira; é o suficiente para separar gente de robô de busca.
  * Retenção: 180 dias. Linhas mais velhas são apagadas na própria escrita.

GeoIP — qual fonte, sem inventar região:

  1. base LOCAL ``dbip-city-lite.mmdb`` (DB-IP City Lite, gratuita, offline) lida
     com o leitor MaxMind do venv ``~/.hermes/venvs/geopainel``. Nenhuma consulta
     externa por visita: o IP do visitante não sai desta máquina.
  2. se a base local não estiver instalada, ``ip-api.com`` (API pública, limite
     ~45 req/min, cache em banco por hash) é usada como reserva — e a tela diz isso.
  3. sem nenhuma das duas: país/estado/cidade ficam vazios e a visita aparece como
     "não identificado". Nunca se chuta região.

Uso como biblioteca::

    import metricas_publico as mp
    registro = mp.registra("189.4.1.1", "Mozilla/5.0 ... Chrome/120 Linux")
    resumo = mp.resumo()

Uso direto (diagnóstico, sem servidor)::

    python3 metricas_publico.py resumo
    python3 metricas_publico.py geo 8.8.8.8
    python3 metricas_publico.py limpar
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(os.environ.get("PAINEL_METRICAS_DIR", Path.home() / ".hermes" / "painel_publico"))
BANCO = Path(os.environ.get("PAINEL_METRICAS_DB", BASE / "visitas.db"))
SAL = Path(os.environ.get("PAINEL_METRICAS_SAL", BASE / ".salt"))
GEO_LOCAL = Path(os.environ.get("PAINEL_GEO_DB", BASE / "geo" / "dbip-city-lite.mmdb"))
VENV_GEO = Path.home() / ".hermes" / "venvs" / "geopainel"

FUSO = timezone(timedelta(hours=-3))          # America/Sao_Paulo, sem horário de verão desde 2019
RETENCAO_DIAS = 180

# Robôs de busca/monitoramento/preview de rede social: ficam registrados com bot=1
# (para transparência) e são EXCLUÍDOS de todas as contagens da tela.
RE_BOT = re.compile(
    r"bot\b|bot/|crawl|spider|slurp|bingpreview|facebookexternalhit|telegrambot|whatsapp|"
    r"headlesschrome|phantomjs|python-requests|python-urllib|aiohttp|curl/|wget/|"
    r"monitor|uptime|pingdom|uptimerobot|semrush|ahrefs|scrapy",
    re.I,
)

_TRAVA = threading.Lock()
_LEITOR = None          # leitor MaxMind aberto (uma vez por processo)
_LEITOR_TENTOU = False
_USA_API = False        # True quando a fonte em uso é a API pública
_CACHE_API: dict[str, tuple[str | None, str | None, str | None]] = {}

TABELAS = """
CREATE TABLE IF NOT EXISTS visitas (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,          -- ISO com fuso -03
    dia      TEXT NOT NULL,          -- AAAA-MM-DD no fuso de Brasília
    ip_hash  TEXT NOT NULL,
    pais     TEXT,                   -- ISO-2 (BR) ou NULL
    uf       TEXT,                   -- estado/província por extenso
    cidade   TEXT,
    ua       TEXT,                   -- resumo, ex.: "Chrome · Linux"
    bot      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_visitas_dia ON visitas(dia);
CREATE INDEX IF NOT EXISTS ix_visitas_hash ON visitas(ip_hash);
CREATE TABLE IF NOT EXISTS geo_cache (
    ip_hash TEXT PRIMARY KEY,
    pais    TEXT,
    uf      TEXT,
    cidade  TEXT
);
"""


# --------------------------------------------------------------------- básico
def agora() -> datetime:
    return datetime.now(FUSO)


def _data_dia(momento: datetime | None = None) -> str:
    return (momento or agora()).strftime("%Y-%m-%d")


def conexao() -> sqlite3.Connection:
    BANCO.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(BANCO, timeout=8)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(TABELAS)
    return con


def _sal() -> str:
    """Sal local do hash. Criado na primeira visita, 0600, nunca versionado."""
    if SAL.exists():
        texto = SAL.read_text(encoding="utf-8").strip()
        if texto:
            return texto
    SAL.parent.mkdir(parents=True, exist_ok=True)
    texto = secrets.token_hex(32)
    SAL.write_text(texto + "\n", encoding="utf-8")
    try:
        os.chmod(SAL, 0o600)
    except OSError:
        pass
    return texto


def hash_ip(ip: str, dia: str | None = None) -> str:
    """Hash do IP com sal e com o dia dentro. Não reversível, não emendável entre dias."""
    return hashlib.sha256(f"{_sal()}|{dia or _data_dia()}|{ip}".encode()).hexdigest()[:32]


def _ip_valido(ip: str | None) -> str | None:
    if not ip:
        return None
    texto = str(ip).strip().strip('"')
    if texto.startswith("::ffff:"):
        texto = texto[7:]
    texto = texto.split("%", 1)[0]
    try:
        return str(ipaddress.ip_address(texto))
    except ValueError:
        return None


def _ip_privado(ip: str) -> bool:
    try:
        end = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return end.is_private or end.is_loopback or end.is_link_local or end.is_multicast


def resumo_ua(ua: str | None) -> str:
    """Navegador · Sistema. Só isso — a string crua não é guardada."""
    texto = (ua or "").strip()
    if not texto:
        return "desconhecido"
    navegador = "outro"
    for chave, nome in (
        ("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"),
        ("Chrome/", "Chrome"), ("Safari/", "Safari"),
    ):
        if chave in texto:
            navegador = nome
            break
    sistema = "outro"
    for chave, nome in (
        ("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"),
        ("Windows", "Windows"), ("Mac OS X", "macOS"), ("CrOS", "ChromeOS"),
        ("Linux", "Linux"),
    ):
        if chave in texto:
            sistema = nome
            break
    return f"{navegador} · {sistema}"


def eh_bot(ua: str | None) -> bool:
    return bool(RE_BOT.search(ua or ""))


# -------------------------------------------------------------------- GeoIP
def _leitor_geo():
    """Abre a base local (uma vez por processo). None se não estiver disponível."""
    global _LEITOR, _LEITOR_TENTOU
    if _LEITOR is not None or _LEITOR_TENTOU:
        return _LEITOR
    _LEITOR_TENTOU = True
    if not GEO_LOCAL.exists():
        return None
    try:
        import maxminddb  # noqa: F401  (instalado no venv dedicado)
    except Exception:
        for pasta in sorted(VENV_GEO.glob("lib/python*/site-packages")):
            if str(pasta) not in sys.path:
                sys.path.insert(0, str(pasta))
        try:
            import maxminddb  # noqa: F401
        except Exception:
            return None
    try:
        _LEITOR = maxminddb.open_database(str(GEO_LOCAL))
    except Exception:
        _LEITOR = None
    return _LEITOR


def fonte_geo() -> str:
    """Texto honesto sobre a fonte de geolocalização em uso (vai para a tela)."""
    if GEO_LOCAL.exists() and _leitor_geo() is not None:
        return "base local DB-IP City Lite (offline — o IP do visitante não sai desta máquina)"
    if _USA_API:
        return "API pública ip-api.com (consulta por visita, com cache) — base local indisponível"
    return "nenhuma — região não identificada"


def _nome(no: dict | None) -> str | None:
    if not isinstance(no, dict):
        return None
    nomes = no.get("names") or {}
    return nomes.get("pt-BR") or nomes.get("en")


def geolocaliza(ip: str | None) -> tuple[str | None, str | None, str | None]:
    """(país ISO-2, estado, cidade). Vazio quando não há base ou não se sabe — nunca chute."""
    ip = _ip_valido(ip)
    if not ip or _ip_privado(ip):
        return None, None, None

    leitor = _leitor_geo()
    if leitor is not None:
        try:
            dados = leitor.get(ip) or {}
        except Exception:
            dados = {}
        pais = ((dados.get("country") or {}).get("iso_code") or None)
        subs = dados.get("subdivisions") or []
        uf = _nome(subs[0]) if subs else None
        cidade = _nome(dados.get("city"))
        return (pais.upper() if pais else None), uf, cidade

    return _geo_por_api(ip)


def _geo_por_api(ip: str) -> tuple[str | None, str | None, str | None]:
    """Reserva: ip-api.com. Guarda o resultado no cache pelo hash, nunca pelo IP."""
    global _USA_API
    chave = hash_ip(ip)
    if chave in _CACHE_API:
        return _CACHE_API[chave]
    saida: tuple[str | None, str | None, str | None] = (None, None, None)
    try:
        url = (f"http://ip-api.com/json/{ip}"
               "?fields=status,countryCode,regionName,city&lang=pt-BR")
        with urllib.request.urlopen(url, timeout=3) as resposta:
            dados = json.loads(resposta.read().decode("utf-8") or "{}")
        if dados.get("status") == "success":
            saida = (dados.get("countryCode"), dados.get("regionName"), dados.get("city"))
            _USA_API = True
    except Exception:
        saida = (None, None, None)
    _CACHE_API[chave] = saida
    try:
        with _TRAVA, conexao() as con:
            con.execute(
                "INSERT OR REPLACE INTO geo_cache(ip_hash, pais, uf, cidade) VALUES (?,?,?,?)",
                (chave, *saida),
            )
    except Exception:
        pass
    return saida


def _do_cache(ip_hash: str) -> tuple[str | None, str | None, str | None] | None:
    try:
        with conexao() as con:
            linha = con.execute(
                "SELECT pais, uf, cidade FROM geo_cache WHERE ip_hash = ?", (ip_hash,)
            ).fetchone()
    except Exception:
        return None
    if not linha:
        return None
    return linha[0], linha[1], linha[2]


# ----------------------------------------------------------------- registra
def registra(ip: str | None, user_agent: str | None, momento: datetime | None = None) -> dict:
    """Grava UMA visita e devolve o que foi gravado (sem IP, obviamente).

    Chamado só na abertura da página (GET /). Robô fica com bot=1 e não entra
    em nenhuma contagem da tela.
    """
    momento = momento or agora()
    dia = _data_dia(momento)
    iplimpo = _ip_valido(ip)
    chave = hash_ip(iplimpo or "sem-ip", dia)

    pais = uf = cidade = None
    if iplimpo:
        em_cache = _do_cache(chave)
        if em_cache:
            pais, uf, cidade = em_cache
        else:
            pais, uf, cidade = geolocaliza(iplimpo)

    bot = 1 if eh_bot(user_agent) else 0
    registro = {
        "ts": momento.replace(microsecond=0).isoformat(),
        "dia": dia,
        "ip_hash": chave,
        "pais": pais,
        "uf": uf,
        "cidade": cidade,
        "ua": resumo_ua(user_agent),
        "bot": bot,
    }
    corte = (momento - timedelta(days=RETENCAO_DIAS)).strftime("%Y-%m-%d")
    with _TRAVA, conexao() as con:
        con.execute(
            "INSERT INTO visitas(ts, dia, ip_hash, pais, uf, cidade, ua, bot)"
            " VALUES (:ts, :dia, :ip_hash, :pais, :uf, :cidade, :ua, :bot)",
            registro,
        )
        con.execute("DELETE FROM visitas WHERE dia < ?", (corte,))
    return registro


# ------------------------------------------------------------------- resumo
def _rotulo_regiao(pais: str | None, uf: str | None, cidade: str | None) -> str:
    if not pais and not uf:
        return "não identificado"
    partes = [p for p in (cidade, uf) if p]
    if not partes:
        return pais or "não identificado"
    if pais and pais != "BR":
        partes.append(pais)
    return " · ".join(partes)


def resumo(dias_serie: int = 14) -> dict:
    """Bloco de métricas da tela: hoje, total, top regiões e série por dia."""
    hoje = agora()
    dia_hoje = _data_dia(hoje)
    dia_ontem = (hoje - timedelta(days=1)).strftime("%Y-%m-%d")
    desde = (hoje - timedelta(days=max(1, dias_serie) - 1)).strftime("%Y-%m-%d")

    with conexao() as con:
        con.row_factory = sqlite3.Row

        def conta(where: str, params: tuple = ()) -> tuple[int, int]:
            linha = con.execute(
                f"SELECT COUNT(*) AS visitas, COUNT(DISTINCT ip_hash) AS pessoas"
                f" FROM visitas WHERE bot = 0 AND {where}",
                params,
            ).fetchone()
            return int(linha["visitas"] or 0), int(linha["pessoas"] or 0)

        visitas_hoje, pessoas_hoje = conta("dia = ?", (dia_hoje,))
        visitas_ontem, pessoas_ontem = conta("dia = ?", (dia_ontem,))
        visitas_total, pessoas_total = conta("1 = 1")
        bots = int(con.execute("SELECT COUNT(*) FROM visitas WHERE bot = 1").fetchone()[0] or 0)
        primeiro = con.execute("SELECT MIN(dia) FROM visitas WHERE bot = 0").fetchone()[0]

        regioes = []
        for linha in con.execute(
            "SELECT pais, uf, cidade, COUNT(*) AS visitas, COUNT(DISTINCT ip_hash) AS pessoas"
            " FROM visitas WHERE bot = 0 GROUP BY pais, uf, cidade"
            " ORDER BY pessoas DESC, visitas DESC LIMIT 30"
        ).fetchall():
            regioes.append({
                "regiao": _rotulo_regiao(linha["pais"], linha["uf"], linha["cidade"]),
                "visitas": int(linha["visitas"]),
                "pessoas": int(linha["pessoas"]),
            })

        # por estado (agregação própria no banco: a lista de cidades pode repetir o estado)
        ufs = []
        for linha in con.execute(
            "SELECT pais, uf, COUNT(*) AS visitas, COUNT(DISTINCT ip_hash) AS pessoas"
            " FROM visitas WHERE bot = 0 GROUP BY pais, uf"
            " ORDER BY pessoas DESC, visitas DESC LIMIT 8"
        ).fetchall():
            ufs.append({
                "regiao": _rotulo_regiao(linha["pais"], linha["uf"], None),
                "visitas": int(linha["visitas"]),
                "pessoas": int(linha["pessoas"]),
            })

        serie = []
        for linha in con.execute(
            "SELECT dia, COUNT(*) AS visitas, COUNT(DISTINCT ip_hash) AS pessoas"
            " FROM visitas WHERE bot = 0 AND dia >= ? GROUP BY dia ORDER BY dia",
            (desde,),
        ).fetchall():
            serie.append({
                "dia": linha["dia"],
                "rotulo": linha["dia"][8:10] + "/" + linha["dia"][5:7],
                "visitas": int(linha["visitas"]),
                "pessoas": int(linha["pessoas"]),
            })

    return {
        "gerado_em": hoje.replace(microsecond=0).isoformat(),
        "hoje": {"dia": dia_hoje, "visitas": visitas_hoje, "pessoas": pessoas_hoje},
        "ontem": {"dia": dia_ontem, "visitas": visitas_ontem, "pessoas": pessoas_ontem},
        "total": {"visitas": visitas_total, "pessoas": pessoas_total, "desde": primeiro},
        "regioes": regioes[:6],
        "estados": ufs,
        "serie": serie,
        "bots_filtrados": bots,
        "fonte_geo": fonte_geo(),
        "retencao_dias": RETENCAO_DIAS,
        "anonimato": (
            "Sem cookies e sem IP guardado: cada visita vira um hash que muda todo dia. "
            "Do visitante só ficam país/estado/cidade, data/hora e navegador resumido."
        ),
    }


def limpar() -> int:
    """Apaga visitas fora da retenção (o servidor já faz isso a cada escrita)."""
    corte = (agora() - timedelta(days=RETENCAO_DIAS)).strftime("%Y-%m-%d")
    with _TRAVA, conexao() as con:
        cur = con.execute("DELETE FROM visitas WHERE dia < ?", (corte,))
    return cur.rowcount or 0


def _cli(argv: list[str]) -> int:
    acao = argv[1] if len(argv) > 1 else "resumo"
    if acao == "resumo":
        print(json.dumps(resumo(), ensure_ascii=False, indent=2))
        return 0
    if acao == "geo":
        if len(argv) < 3:
            print("uso: metricas_publico.py geo <ip>")
            return 2
        pais, uf, cidade = geolocaliza(argv[2])
        print(json.dumps({"pais": pais, "uf": uf, "cidade": cidade,
                          "fonte": fonte_geo()}, ensure_ascii=False))
        return 0
    if acao == "limpar":
        print(f"removidas {limpar()} linhas fora da retenção de {RETENCAO_DIAS} dias")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv))
