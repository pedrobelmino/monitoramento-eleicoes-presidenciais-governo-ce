#!/usr/bin/env python3
"""Estatísticas dos cargos proporcionais do Ceará — Senador, Dep. Federal e Dep. Estadual.

Lê o mesmo feed oficial do TSE que o resto do painel (`resultados.tse.jus.br/oficial/ele2026`),
no qual a eleição 6259 serve aos quatro cargos do estado:

    cargo 0003 -> Governador         (já usado na aba 1)
    cargo 0005 -> Senador
    cargo 0006 -> Deputado Federal
    cargo 0007 -> Deputado Estadual

O que é mostrado:
  * os mais votados de cada cargo (com foto, partido, votos e percentual);
  * a votação somada por partido e quantos candidatos cada um lançou — é a base do
    quociente eleitoral, mas a atribuição de vagas só sai no fim da apuração, então
    aqui não se afirma quem se elegeu: mostra-se a votação da legenda.

Uso:  python estatisticas_ce.py
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import painel_eleicao as pe  # noqa: E402

ELEICAO = "6259"
UF = "ce"

CARGOS = [
    ("0005", "senador", "SENADOR", 8),
    ("0006", "federal", "DEPUTADO FEDERAL", 8),
    ("0007", "estadual", "DEPUTADO ESTADUAL", 8),
]


def _votos(c: dict) -> int:
    try:
        return int(str(c.get("votos", "0")).replace(".", "").replace(",", ""))
    except Exception:
        return 0


def _pct(c: dict) -> float:
    try:
        return float(str(c.get("pct", "0")).replace(",", "."))
    except Exception:
        return 0.0


# número do partido -> sigla, para ligar as federações do arquivo do TSE às siglas
# que aparecem nos candidatos. Só os partidos que formam federação em 2026.
NUM_PARTIDO = {
    "13": "PT", "65": "PC DO B", "43": "PV", "18": "REDE", "50": "PSOL",
    "11": "PP", "44": "UNIÃO", "23": "CIDADANIA", "45": "PSDB",
    "25": "PRD", "77": "SOLIDARIEDADE",
}


def _mapa_federacoes(d: dict) -> dict:
    """sigla do partido -> sigla da federação (ou o próprio partido)."""
    mapa: dict[str, str] = {}
    for fed in ((d.get("carg") or [{}])[0].get("fed") or []):
        sg = fed.get("sg") or fed.get("nm") or ""
        for np in fed.get("npar") or []:
            sig = NUM_PARTIDO.get(str(np))
            if sig and sg:
                mapa[sig] = sg
    return mapa


def _projecao(cs: list[dict], vagas: int, fed: dict, validos: int, majoritario: bool) -> dict:
    """Quem entra na projeção de vagas, com as regras brasileiras.

    Senador é majoritário: os mais votados levam as vagas. Deputados são
    proporcionais: quociente eleitoral, federações somando juntas e, dentro de
    cada grupo, as maiores médias — com o piso de 10% do quociente por candidato.
    A projeção usa a votação de agora; muda enquanto a apuração corre.
    """
    if not vagas or not validos:
        return {"qe": 0.0, "piso": 0.0, "eleitos": set(), "grupos": {}}

    if majoritario:
        ordem = sorted(cs, key=lambda c: -_votos(c))
        sel = ordem[:vagas]
        return {
            "qe": 0.0, "piso": 0.0, "eleitos": {c.get("sq") for c in sel},
            "grupos": {}, "regra": f"majoritário — {vagas} mais votados",
        }

    qe = validos / vagas
    piso = 0.10 * qe

    grupos: dict[str, dict] = {}
    for c in cs:
        sig = (c.get("partido") or "—").strip().upper()
        chave = fed.get(sig, sig)
        g = grupos.setdefault(chave, {"votos": 0, "cands": [], "sigla": sig})
        g["votos"] += _votos(c)
        g["cands"].append(c)

    elegiveis = {k: v for k, v in grupos.items() if v["votos"] >= qe}
    # só concorre a vaga quem tem candidato acima do piso de 10% do quociente
    aptos = {k: sorted([c for c in v["cands"] if _votos(c) >= piso], key=lambda c: -_votos(c))
             for k, v in elegiveis.items()}
    conquistadas = {k: 0 for k in elegiveis}
    dadas = 0
    while dadas < vagas:
        # a vaga vai para a maior média entre os grupos que ainda têm candidato apto
        candidatos = {k: v for k, v in elegiveis.items() if len(aptos[k]) > conquistadas[k]}
        if not candidatos:
            break
        melhor = max(candidatos, key=lambda k: candidatos[k]["votos"] / (conquistadas[k] + 1))
        conquistadas[melhor] += 1
        dadas += 1

    eleitos = set()
    for chave, n in conquistadas.items():
        for c in aptos.get(chave, [])[:n]:
            eleitos.add(c.get("sq"))

    return {
        "qe": qe, "piso": piso, "eleitos": eleitos, "grupos": conquistadas,
        "regra": f"quociente {qe:,.0f} · piso por candidato {piso:,.0f} · {len(conquistadas)} grupos acima do quociente",
    }


def _um_cargo(spec: tuple) -> tuple[str, dict]:
    cargo, chave, rotulo, quantos = spec
    d = pe.pega_resultado(ELEICAO, UF, cargo)
    if not d:
        return chave, {"erro": True, "rotulo": rotulo}
    cs = pe.candidatos(d, cargo) or []
    s = d.get("s", {}) or {}
    v = d.get("v", {}) or {}
    cg = (d.get("carg") or [{}])[0]
    vagas = int(cg.get("nv") or 0)
    majoritario = str(cargo) == "0005"   # senador: os mais votados levam as vagas

    por_partido: dict[str, dict] = {}
    for c in cs:
        p = (c.get("partido") or "—").strip().upper()
        reg = por_partido.setdefault(p, {"partido": p, "votos": 0, "candidatos": 0})
        reg["votos"] += _votos(c)
        reg["candidatos"] += 1

    total = int(v.get("vv", "0") or 0) or sum(_votos(c) for c in cs)
    proj = _projecao(cs, vagas, _mapa_federacoes(d), total, majoritario)
    leiaute = []
    for c in cs[:quantos]:
        foto = None
        try:
            foto = pe.foto_candidato(ELEICAO, UF, c.get("sq"))
        except Exception:
            foto = None
        leiaute.append({
            "nome": str(c.get("nome", "")).title(),
            "partido": (c.get("partido") or "").strip().upper(),
            "votos": _votos(c),
            "pct": _pct(c),
            "foto": foto,
            "projetado": c.get("sq") in proj["eleitos"],
        })

    partidos = sorted(por_partido.values(), key=lambda x: -x["votos"])
    for p in partidos:
        p["pct"] = (100.0 * p["votos"] / total) if total else 0.0

    return chave, {
        "rotulo": rotulo,
        "uf": "CE",
        "pct_apurado": s.get("pst", "0"),
        "secoes": s.get("st"), "secoes_total": s.get("ts"),
        "validos": total,
        "candidatos": len(cs),
        "mais_votados": leiaute,
        "partidos": partidos[:12],
        "vagas": vagas,
        "quociente": proj["qe"],
        "piso": proj["piso"],
        "regra": proj.get("regra", ""),
        "projetados": len(proj["eleitos"]),
        "grupos": sorted(
            [{"grupo": k, "cadeiras": n} for k, n in (proj.get("grupos") or {}).items()],
            key=lambda x: -x["cadeiras"]),
        "eleitos_lista": sorted(
            [{"nome": str(c.get("nome", "")).title(),
              "partido": (c.get("partido") or "").strip().upper(),
              "votos": _votos(c), "pct": _pct(c)}
             for c in cs if c.get("sq") in proj["eleitos"]],
            key=lambda x: -x["votos"]),
    }


def coleta() -> dict:
    """Devolve {'senador': {...}, 'federal': {...}, 'estadual': {...}}."""
    with ThreadPoolExecutor(max_workers=3) as ex:
        res = dict(ex.map(_um_cargo, CARGOS))
    return res


if __name__ == "__main__":
    r = coleta()
    for chave, d in r.items():
        if d.get("erro"):
            print(f"  {chave}: sem dados")
            continue
        print(f"\n  === {d['rotulo']} ({d['pct_apurado']}% apurado) ===")
        print(f"  validos: {d['validos']:,} | candidatos: {d['candidatos']}".replace(",", "."))
        for c in d["mais_votados"][:5]:
            print(f"    {c['nome'][:26]:28s} {c['partido']:12s} {c['pct']:6.2f}%  {c['votos']:,}".replace(",", "."))
        print("  por partido:", ", ".join(f"{p['partido']} {p['pct']:.1f}%" for p in d["partidos"][:6]))
