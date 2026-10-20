#!/usr/bin/env python3
"""Monta o retrato público do painel — o JSON que a página abre para o mundo.

Diferença deste módulo para o painel interno: aqui não existe nada de pessoal,
nem caminho interno, nem segredo. Entra apenas o que o TSE publica, mais contas
derivadas marcadas como contas.

Regra de ouro do projeto (inegociável): dados só do feed público do TSE.
Campo que o TSE não publicou fica ``null`` e a tela diz "sem dado". Estimativa,
quando aparece, sai rotulada com a premissa junto.

Fluxo:

  1. pergunta ao TSE qual eleição está ativa (``eleicao_ativa``: 6258 = 2º turno,
     6257 = 1º turno);
  2. se a ativa já tem seção apurada, o quadro é ela;
     se ela existe mas está em ``and="n"`` (não iniciada), o quadro principal passa
     a ser o 1º turno ENCERRADO e o 2º turno entra como aviso — nunca se mostra um
     placar de zeros como se fosse resultado;
  3. agrega por região somando as 27 UFs (o TSE não publica arquivo por região);
  4. gera os insights como contas a partir dos números — nenhuma opinião;
  5. anexa a geometria do mapa (se ``dados/mapa_ufs.json`` existir) e a tabela de
     fontes (TRE de cada estado).

Uso::

    python3 snapshot_publico.py                 # grava dados/publico.json
    python3 snapshot_publico.py --saida /tmp/x.json --verboso
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

SAIDA = Path(os.environ.get("PAINEL_PUBLICO_JSON", AQUI / "dados" / "publico.json"))
SERIE = Path(os.environ.get("PAINEL_SERIE", Path.home() / ".hermes" / "eleicao_presidente"
                            / "serie_presidente.jsonl"))
MAPA = Path(os.environ.get("PAINEL_MAPA", AQUI / "dados" / "mapa_ufs.json"))
FUSO = timezone(timedelta(hours=-3))

REGIOES_ORDEM = ["Norte", "Nordeste", "Centro-Oeste", "Sudeste", "Sul"]
REGIOES = {
    "Norte": ["AC", "AP", "AM", "PA", "RO", "RR", "TO"],
    "Nordeste": ["AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"],
    "Centro-Oeste": ["DF", "GO", "MT", "MS"],
    "Sudeste": ["ES", "MG", "RJ", "SP"],
    "Sul": ["PR", "SC", "RS"],
}
REGIAO_DE = {uf: reg for reg, ufs in REGIOES.items() for uf in ufs}
UFS_27 = [uf for reg in REGIOES.values() for uf in reg]
# URL pública do TSE — o MESMO padrão documentado no AGENTS.md do projeto (nunca sonde
# URL nova: requisição a caminho inexistente bloqueia o IP).
TSE_BASE = "https://resultados.tse.jus.br/oficial/ele2026"
ANDAMENTO = {
    "f": "apuração encerrada",
    "s": "apuração em andamento",
    "n": "apuração não iniciada",
}


# ---------------------------------------------------- coletor de reserva (embutido)
# Por que existe: o painel público não pode depender de um módulo que ainda não está
# no repositório. Se ``coleta_presidente.py`` existir, ele é usado (tem mais campos e
# a série oficial); se não existir, este coletor faz a mesma leitura — 28 arquivos
# públicos do TSE (27 UFs + nacional), mesma URL documentada no AGENTS.md, campos
# ausentes viram None (nunca zero). Nada aqui inventa, interpola ou estima.
def _json_tse(uf: str, eleicao: str) -> str:
    """URL do arquivo público. No diretório o código vai curto (6257) e no NOME do
    arquivo vai com 6 dígitos (``ce-c0001-e006257-u.json``) — ``e6257`` dá 404."""
    codigo = f"{int(eleicao):06d}"
    uf = uf.lower()
    return f"{TSE_BASE}/{eleicao}/dados/{uf}/{uf}-c0001-e{codigo}-u.json"


def _baixa_json(url: str, tentativas: int = 2, timeout: int = 25) -> dict | None:
    import urllib.error
    import urllib.request
    for tentativa in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "painel-eleicao-publico/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resposta:
                return json.loads(resposta.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:          # 404 não repete: caminho não existe
                return None
        except Exception:
            pass
        if tentativa + 1 < tentativas:
            import time as _t
            _t.sleep(1.5)
    return None


def _int(valor):
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return None


def _flt(valor):
    try:
        return round(float(str(valor).replace(",", ".").strip()), 2)
    except (TypeError, ValueError):
        return None


def _cands(dados: dict) -> list[dict]:
    cargos = dados.get("carg") or []
    cargo = next((c for c in cargos if str(c.get("cd")) == "1"), cargos[0] if cargos else {})
    lista = []
    for agr in cargo.get("agr") or []:
        for par in agr.get("par") or []:
            for cand in par.get("cand") or []:
                lista.append({
                    "sq": str(cand.get("sqcand") or ""),
                    "nome": cand.get("nmu") or cand.get("nm") or "?",
                    "partido": par.get("sg") or "",
                    "votos": _int(cand.get("vap")) or 0,
                    "pct": _flt(cand.get("pvap")),
                })
    lista.sort(key=lambda c: -c["votos"])
    return lista


def _bloco(dados: dict) -> dict:
    s = dados.get("s") or {}
    v = dados.get("v") or {}
    e = dados.get("e") or {}
    st, ts = _int(s.get("st")), _int(s.get("ts"))
    return {
        "st": st, "ts": ts,
        "pct_apurado": _flt(s.get("pst")) if s.get("pst") is not None else (
            round(100.0 * st / ts, 2) if st is not None and ts else None),
        "vv": _int(v.get("vv")), "tv": _int(v.get("tv")),
        "vb": _int(v.get("vb")), "tvn": _int(v.get("tvn")),
        "te": _int(e.get("te")), "comparecimento": _int(e.get("c")),
        "candidatos": _cands(dados),
    }


def _data_fonte(dados: dict) -> tuple[str | None, str | None]:
    dia, hora = dados.get("dg"), dados.get("hg")
    if not dia or not hora:
        return None, None
    texto = f"{dia} {hora}"
    try:
        return datetime.strptime(texto, "%d/%m/%Y %H:%M:%S").strftime("%Y-%m-%dT%H:%M:%S"), texto
    except ValueError:
        return None, texto


def coleta_local(eleicao: str) -> dict:
    from concurrent.futures import ThreadPoolExecutor
    eleicao = str(eleicao)
    alvos = [("BR", "br")] + [(uf, uf.lower()) for uf in UFS_27]
    resultados, erros = {}, []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futuros = {executor.submit(_baixa_json, _json_tse(uf, eleicao)): rotulo
                   for rotulo, uf in alvos}
        for futuro in futuros:
            rotulo = futuros[futuro]
            try:
                resultados[rotulo] = futuro.result()
            except Exception as exc:
                resultados[rotulo] = None
                erros.append(f"{rotulo}: {type(exc).__name__}")
    bruto_br = resultados.get("BR")
    fonte_iso = fonte_texto = andamento = None
    nacional = None
    if bruto_br is not None:
        fonte_iso, fonte_texto = _data_fonte(bruto_br)
        andamento = bruto_br.get("and")
        nacional = _bloco(bruto_br)
    ufs = {}
    for uf in UFS_27:
        dados = resultados.get(uf)
        if dados is None:
            erros.append(f"{uf}: sem dado")
            continue
        bloco = _bloco(dados)
        ufs[uf] = {"regiao": REGIAO_DE[uf], **bloco}
    return {
        "coletado_em": datetime.now(FUSO).replace(microsecond=0).isoformat(),
        "eleicao": eleicao,
        "turno": {"6257": "1º turno", "6258": "2º turno"}.get(eleicao, f"turno {eleicao}"),
        "fonte_gerada_em": fonte_iso, "fonte_gerada_texto": fonte_texto,
        "andamento": andamento, "nacional": nacional, "ufs": ufs, "erros": erros,
        "conferencia": None,
    }


def eleicao_ativa_local() -> str:
    dados = _baixa_json(_json_tse("br", "6258"))
    if dados:
        st = _int((dados.get("s") or {}).get("st"))
        if (st is not None and st > 0) or (dados.get("and") or "").lower() == "f":
            return "6258"
    return "6257"


def serie_local(eleicao: str, limite: int = 400) -> list[dict]:
    if not SERIE.exists():
        return []
    registros: list[dict] = []
    try:
        for linha in SERIE.read_text(encoding="utf-8").splitlines()[-limite:]:
            linha = linha.strip()
            if not linha:
                continue
            try:
                reg = json.loads(linha)
            except json.JSONDecodeError:
                continue
            if isinstance(reg, dict) and str(reg.get("eleicao")) == str(eleicao):
                registros.append(reg)
    except OSError:
        return []
    return registros


def grava_serie_local(snap: dict) -> bool:
    """Acrescenta UMA linha no JSONL só quando a fonte do TSE mudou de verdade.

    Sem isso a "linha do tempo" nunca teria memória no modo de reserva — e enchendo
    o arquivo de cópias idênticas a cada 15 min ela mentiria sobre ter evoluído.
    """
    marca = (str(snap.get("eleicao")), snap.get("fonte_gerada_em"))
    if not marca[1]:
        return False
    if SERIE.exists():
        try:
            ultimas = SERIE.read_text(encoding="utf-8").splitlines()[-1:]
            if ultimas:
                ultimo = json.loads(ultimas[0])
                if (str(ultimo.get("eleicao")), ultimo.get("fonte_gerada_em")) == marca:
                    return False
        except (OSError, json.JSONDecodeError):
            pass
    SERIE.parent.mkdir(parents=True, exist_ok=True)
    with SERIE.open("a", encoding="utf-8") as arquivo:
        arquivo.write(json.dumps(snap, ensure_ascii=False) + "\n")
    return True


def _motor():
    """Devolve (coleta, ativa, serie, grava) — o módulo da missão por-UF se existir, ou
    o coletor embutido. Assim o painel público nunca fica refém de um arquivo ausente."""
    try:
        import coleta_presidente as cp
        return (cp.coleta, cp.eleicao_ativa,
                lambda ele, lim=400: cp.serie(SERIE, eleicao=ele, limite=lim),
                lambda snap: cp.grava_serie(snap, SERIE))
    except ImportError:
        return (coleta_local, eleicao_ativa_local,
                lambda ele, lim=400: serie_local(ele, lim),
                grava_serie_local)


# --------------------------------------------------------------------- formatação
def milhar(valor) -> str:
    if valor is None:
        return "—"
    return f"{int(valor):,}".replace(",", ".")


def pct(valor, casas: int = 1) -> str:
    if valor is None:
        return "—"
    return f"{valor:.{casas}f}".replace(".", ",")


def _num(valor):
    return valor if isinstance(valor, (int, float)) else None


# ------------------------------------------------------------------- agregação
def agrega_regioes(ufs: dict, vv_nacional: int | None) -> list[dict]:
    """Soma as UFs de cada região. Só aparece região com pelo menos uma UF coletada."""
    por_regiao: dict[str, dict] = {}
    for uf, dados in (ufs or {}).items():
        regiao = dados.get("regiao") or "?"
        g = por_regiao.setdefault(regiao, {
            "regiao": regiao, "ufs": [], "st": 0, "ts": 0, "vv": 0,
            "te": 0, "comparecimento": 0, "candidatos": {},
        })
        g["ufs"].append(uf)
        for campo in ("st", "ts", "vv", "te", "comparecimento"):
            g[campo] += int(dados.get(campo) or 0)
        for cand in dados.get("candidatos") or []:
            sq = str(cand.get("sq") or cand.get("nome"))
            item = g["candidatos"].setdefault(sq, {
                "sq": sq, "nome": cand.get("nome"), "partido": cand.get("partido"), "votos": 0,
            })
            item["votos"] += int(cand.get("votos") or 0)

    saida = []
    for regiao, g in por_regiao.items():
        cands = sorted(g["candidatos"].values(), key=lambda c: -c["votos"])
        for c in cands:
            c["pct"] = round(100.0 * c["votos"] / g["vv"], 2) if g["vv"] else None
        g["candidatos"] = cands[:3]           # tela pública: o pódio basta
        g["pct_apurado"] = round(100.0 * g["st"] / g["ts"], 2) if g["ts"] else None
        g["secoes_faltantes"] = (g["ts"] - g["st"]) if g["ts"] else None
        g["votos_por_secao"] = round(g["vv"] / g["st"], 1) if g["st"] else None
        g["share_votos"] = round(100.0 * g["vv"] / vv_nacional, 2) if vv_nacional else None
        g["n_ufs"] = len(g["ufs"])
        g["ufs"] = sorted(g["ufs"])
        saida.append(g)
    ordem = {nome: i for i, nome in enumerate(REGIOES_ORDEM)}
    saida.sort(key=lambda g: ordem.get(g["regiao"], 99))
    return saida


def quadro_ufs(ufs: dict) -> list[dict]:
    """Lista por UF com a margem do momento (conta derivada, marcada na tela)."""
    lista = []
    for uf, d in (ufs or {}).items():
        cands = sorted(d.get("candidatos") or [], key=lambda c: -int(c.get("votos") or 0))
        primeiro = cands[0] if cands else None
        segundo = cands[1] if len(cands) > 1 else None
        margem = None
        if primeiro and segundo:
            margem = int(primeiro.get("votos") or 0) - int(segundo.get("votos") or 0)
        lista.append({
            "uf": uf,
            "regiao": d.get("regiao"),
            "pct_apurado": d.get("pct_apurado"),
            "st": d.get("st"),
            "ts": d.get("ts"),
            "vv": d.get("vv"),
            "candidatos": cands[:2],
            "lider": (primeiro or {}).get("nome"),
            "lider_pct": (primeiro or {}).get("pct"),
            "margem_votos": margem,
        })
    lista.sort(key=lambda u: (-(u["vv"] or 0), u["uf"]))
    return lista


# -------------------------------------------------------------------- insights
def monta_insights(nacional: dict, regioes: list[dict], ufs: list[dict],
                   proximo: dict | None) -> list[dict]:
    """Contas a partir do dado. Cada item diz se é DADO, CONTA ou ESTIMATIVA."""
    itens: list[dict] = []

    def add(texto: str, tipo: str = "conta") -> None:
        itens.append({"texto": texto, "tipo": tipo})

    cands = nacional.get("candidatos") or []
    a = cands[0] if cands else None
    b = cands[1] if len(cands) > 1 else None

    if proximo:
        add(
            f"O {proximo['turno']} ainda não começou: o TSE está em "
            f"{ANDAMENTO.get(proximo.get('andamento'), proximo.get('andamento') or 'sem informação')} "
            f"({milhar(proximo.get('st'))} de {milhar(proximo.get('ts'))} seções). "
            f"O placar abaixo é o {nacional.get('turno')} encerrado.", "dado")

    add(
        f"Apuração do {nacional.get('turno')}: {pct(nacional.get('pct_apurado'))}% das seções "
        f"({milhar(nacional.get('st'))} de {milhar(nacional.get('ts'))}) — "
        f"{ANDAMENTO.get(nacional.get('andamento'), 'sem informação')}.", "dado")

    if nacional.get("te") and nacional.get("comparecimento"):
        absten = nacional["te"] - nacional["comparecimento"]
        add(
            f"{milhar(nacional.get('comparecimento'))} eleitores compareceram de "
            f"{milhar(nacional.get('te'))} — abstenção de {milhar(absten)} "
            f"({pct(100.0 * absten / nacional['te'], 2)}%).", "conta")

    if a and b:
        dif = int(a["votos"]) - int(b["votos"])
        pp = (float(a["pct"] or 0) - float(b["pct"] or 0))
        add(
            f"{a['nome']} ({a['partido']}) termina {milhar(a['votos'])} votos "
            f"({pct(a['pct'], 2)}%), {milhar(abs(dif))} votos à frente de "
            f"{b['nome']} ({b['partido']}, {pct(b['pct'], 2)}%) — "
            f"{pct(abs(pp), 2)} ponto{'s' if abs(pp) >= 1 else ''} de diferença.", "conta")

    # peso de cada região no total nacional — a mais pesada e a mais leve
    com_share = [r for r in regioes if r.get("share_votos") is not None]
    if com_share:
        pesada = max(com_share, key=lambda r: r["share_votos"])
        leve = min(com_share, key=lambda r: r["share_votos"])
        add(
            f"O {pesada['regiao']} é o maior colégio eleitoral do país: "
            f"{pct(pesada['share_votos'])}% dos votos válidos "
            f"({milhar(pesada['vv'])} votos em {pesada['n_ufs']} estados).", "conta")
        if leve is not pesada:
            add(
                f"O menor peso é o {leve['regiao']}: {pct(leve['share_votos'])}% dos votos "
                f"válidos ({milhar(leve['vv'])} votos).", "conta")

    # onde cada candidato vai melhor / pior (por % dentro da região)
    if a and b:
        com_dado = [r for r in regioes if r.get("candidatos")]
        if com_dado:
            def pcts(r):
                m = {c["nome"]: c.get("pct") for c in r["candidatos"]}
                return m.get(a["nome"]), m.get(b["nome"])
            ricos = [(r, *pcts(r)) for r in com_dado]
            ricos = [x for x in ricos if x[1] is not None and x[2] is not None]
            if ricos:
                melhor = max(ricos, key=lambda x: x[1])
                pior = min(ricos, key=lambda x: x[1])
                add(
                    f"{a['nome']} vai melhor no {melhor[0]['regiao']} "
                    f"({pct(melhor[1])}% contra {pct(melhor[2])}% de {b['nome']}) e pior no "
                    f"{pior[0]['regiao']} ({pct(pior[1])}% contra {pct(pior[2])}%).", "conta")
                virou = max(ricos, key=lambda x: x[2])
                add(
                    f"O melhor terreno de {b['nome']} é o {virou[0]['regiao']}: "
                    f"{pct(virou[2])}% dos votos válidos de lá.", "conta")

    # quanto falta apurar, por região — só onde falta alguma coisa
    for r in sorted(regioes, key=lambda g: -(g.get("secoes_faltantes") or 0)):
        faltam = r.get("secoes_faltantes")
        if not faltam:
            continue
        vps = r.get("votos_por_secao")
        estimativa = f" (~{milhar(int(faltam * vps))} votos pela média de {pct(vps)} votos por seção da região)" if vps else ""
        add(
            f"Faltam {milhar(faltam)} seções no {r['regiao']}{estimativa} — "
            f"estimativa, não resultado.", "estimativa")

    # extremos por UF
    com_margem = [u for u in ufs if u.get("margem_votos") is not None]
    if len(com_margem) > 1:
        estreita = min(com_margem, key=lambda u: abs(u["margem_votos"]))
        folgada = max(com_margem, key=lambda u: abs(u["margem_votos"]))
        add(
            f"Disputa mais apertada: {estreita['uf']}, "
            f"{milhar(abs(estreita['margem_votos']))} votos de diferença "
            f"({estreita['lider']}, {pct(estreita['lider_pct'])}%).", "conta")
        add(
            f"Maior folga: {folgada['uf']}, {milhar(abs(folgada['margem_votos']))} votos "
            f"({folgada['lider']}, {pct(folgada['lider_pct'])}%).", "conta")

    return itens


# ---------------------------------------------------------------------- montar
def _tem_votos(snap: dict) -> bool:
    nacional = snap.get("nacional") or {}
    return bool((nacional.get("st") or 0) > 0 or (nacional.get("vv") or 0) > 0)


def _normaliza(snap: dict) -> dict:
    nacional = snap.get("nacional") or {}
    return {
        "eleicao": snap.get("eleicao"),
        "turno": snap.get("turno"),
        "andamento": snap.get("andamento"),
        "fonte_gerada_em": snap.get("fonte_gerada_em"),
        "fonte_gerada_texto": snap.get("fonte_gerada_texto"),
        "pct_apurado": nacional.get("pct_apurado"),
        "st": nacional.get("st"),
        "ts": nacional.get("ts"),
        "vv": nacional.get("vv"),
        "tv": nacional.get("tv"),
        "vb": nacional.get("vb"),
        "tvn": nacional.get("tvn"),
        "te": nacional.get("te"),
        "comparecimento": nacional.get("comparecimento"),
        "candidatos": nacional.get("candidatos") or [],
    }


def _serie_publica(serie_fn, eleicao: str) -> list[dict]:
    """Série temporal REAL (JSONL append-only). Só pontos distintos da fonte."""
    try:
        registros = serie_fn(eleicao)
    except Exception:
        return []
    pontos, vistos = [], set()
    for reg in registros:
        marca = reg.get("fonte_gerada_em") or reg.get("coletado_em")
        if not marca or marca in vistos:
            continue
        vistos.add(marca)
        nac = reg.get("nacional") or {}
        pontos.append({
            "em": marca,
            "coletado_em": reg.get("coletado_em"),
            "pct_apurado": nac.get("pct_apurado"),
            "vv": nac.get("vv"),
            "candidatos": [{"nome": c.get("nome"), "pct": c.get("pct"),
                            "votos": c.get("votos")}
                           for c in (nac.get("candidatos") or [])[:2]],
        })
    return pontos


def _mapa() -> dict | None:
    if not MAPA.exists():
        return None
    try:
        dados = json.loads(MAPA.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(dados.get("ufs"), dict):
        return None
    return {"viewBox": dados.get("viewBox", "0 0 1000 1030"),
            "ufs": {uf: {"d": v.get("d"), "cx": v.get("cx"), "cy": v.get("cy")}
                    for uf, v in dados["ufs"].items() if v.get("d")}}


def _fontes(eleicao: str) -> list[dict]:
    try:
        import fontes_ufs as fu
        return fu.fontes(eleicao)
    except Exception:
        return []


def montar(verboso: bool = False) -> dict:
    coleta, ativa_fn, serie_fn, grava_serie = _motor()

    ativa = ativa_fn()
    snap = coleta(ativa)
    principal = snap
    proximo = None

    if not _tem_votos(snap):
        # a eleição ativa existe mas não começou: mostra o pleito encerrado e avisa
        proximo = {
            "eleicao": snap.get("eleicao"),
            "turno": snap.get("turno"),
            "andamento": snap.get("andamento"),
            "st": (snap.get("nacional") or {}).get("st"),
            "ts": (snap.get("nacional") or {}).get("ts"),
            "fonte_gerada_texto": snap.get("fonte_gerada_texto"),
        }
        try:
            principal = coleta("6257" if str(snap.get("eleicao")) == "6258" else "6258")
        except Exception as exc:            # rede caiu no meio: fica o que temos
            if verboso:
                print(f"  aviso: coleta do pleito anterior falhou: {exc}")
            principal = snap

    nacional = _normaliza(principal)
    vv_nacional = nacional.get("vv")
    ufs_brutas = principal.get("ufs") or {}
    regioes = agrega_regioes(ufs_brutas, vv_nacional)
    ufs = quadro_ufs(ufs_brutas)
    # série append-only: só cresce quando o TSE publica uma parcial nova
    try:
        if grava_serie and grava_serie(principal) and verboso:
            print(f"  série: novo ponto gravado ({principal.get('fonte_gerada_texto')})")
    except Exception as exc:
        if verboso:
            print(f"  aviso: não gravou a série: {exc}")
    serie = _serie_publica(serie_fn, str(nacional.get("eleicao")))

    if verboso:
        print(f"  eleicao ativa {ativa} · quadro {nacional.get('eleicao')} "
              f"({nacional.get('turno')}) · {len(ufs_brutas)} UFs · "
              f"{nacional.get('pct_apurado')}% apurado · série {len(serie)} pontos")
        if principal.get("erros"):
            print(f"  erros de coleta: {principal['erros']}")

    return {
        "gerado_em": datetime.now(FUSO).replace(microsecond=0).isoformat(),
        "fonte": "TSE — resultados.tse.jus.br (arquivos públicos por UF)",
        "fonte_gerada_texto": nacional.get("fonte_gerada_texto"),
        "eleicao": nacional.get("eleicao"),
        "turno": nacional.get("turno"),
        "andamento": nacional.get("andamento"),
        "proximo": proximo,
        "nacional": {k: nacional[k] for k in
                     ("pct_apurado", "st", "ts", "vv", "tv", "vb", "tvn", "te",
                      "comparecimento", "candidatos")},
        "regioes": regioes,
        "ufs": ufs,
        "insights": monta_insights(nacional, regioes, ufs, proximo),
        "serie": serie,
        "mapa": _mapa(),
        "fontes_ufs": _fontes(str(nacional.get("eleicao") or "6257")),
        "conferencia": principal.get("conferencia"),
        "erros_coleta": principal.get("erros") or [],
    }


def grava(dados: dict, caminho: Path = SAIDA) -> Path:
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho.with_suffix(caminho.suffix + ".tmp")
    temporario.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    temporario.replace(caminho)          # troca atômica: o servidor nunca lê arquivo pela metade
    return caminho


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    saida = SAIDA
    verboso = "--verboso" in argv or "-v" in argv
    if "--saida" in argv:
        saida = Path(argv[argv.index("--saida") + 1])
    try:
        dados = montar(verboso=verboso)
    except Exception as exc:
        print(f"falha ao montar o retrato público: {type(exc).__name__}: {exc}")
        if verboso:
            traceback.print_exc()
        return 1
    grava(dados, saida)
    nac = dados["nacional"]
    print(f"{dados['turno']} · {len(dados['ufs'])} UFs · "
          f"{pct(nac.get('pct_apurado'))}% apurado · "
          f"fonte TSE {dados.get('fonte_gerada_texto') or '—'}")
    for c in nac.get("candidatos", [])[:2]:
        print(f"  {c['nome']} ({c['partido']}): {milhar(c['votos'])} · {pct(c['pct'], 2)}%")
    print(f"{len(dados['insights'])} insights · série {len(dados['serie'])} ponto(s) · "
          f"mapa {'sim' if dados.get('mapa') else 'não'} · gravado em {saida}")
    if dados["erros_coleta"]:
        print(f"erros de coleta (não derrubam a página): {dados['erros_coleta']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
