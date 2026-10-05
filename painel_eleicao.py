#!/usr/bin/env python3
"""Painel das Eleições 2026 — imagem consolidada, enviada a cada 5 minutos.

Fonte: arquivos JSON oficiais do TSE (resultados.tse.jus.br/oficial/ele2026).
Nada é estimado: % apurado, seções, votos e fotos vêm do próprio TSE.

Vale só para 04/10/2026 (1º turno). Fora desse dia o script sai em silêncio.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = "https://resultados.tse.jus.br/oficial/ele2026"
RAIZ = Path.home() / ".hermes" / "eleicao"
FOTOS = RAIZ / "fotos"
SAIDA = RAIZ / "painel-eleicoes.png"
DIA = "2026-10-04"

# (apelido, eleição, UF, cargo, rótulo, abrangência do arquivo)
CORRIDAS = [
    ("presidente", "6257", "br", "0001", "PRESIDENTE DA REPÚBLICA"),
    ("governador_ce", "6259", "ce", "0003", "GOVERNADOR — CEARÁ"),
    ("governador_sp", "6259", "sp", "0003", "GOVERNADOR — SÃO PAULO"),
    ("governador_rs", "6259", "rs", "0003", "GOVERNADOR — RIO GRANDE DO SUL"),
    ("governador_pb", "6259", "pb", "0003", "GOVERNADOR — PARAÍBA"),
    ("governador_rj", "6259", "rj", "0003", "GOVERNADOR — RIO DE JANEIRO"),
]

F_BOLD = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
F_REG = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
LARANJA = (235, 110, 30)
AZUL = (28, 62, 120)
CINZA = (110, 118, 128)
TINTA = (24, 28, 34)
FUNDO = (247, 248, 250)
BRANCO = (255, 255, 255)


def fonte(caminho: str, tam: int):
    try:
        return ImageFont.truetype(caminho, tam)
    except Exception:
        return ImageFont.load_default()


def baixa(url: str, timeout: int = 30) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception:
        return None


def pega_resultado(ele: str, uf: str, cargo: str) -> dict | None:
    """arquivo unificado: <uf>-c<cargo>-e<eleicao>-u.json"""
    nome = f"{uf}-c{cargo}-e{ele.ljust(6, '0')}-u.json"
    # o nome real usa 6 dígitos: e006257
    nome = f"{uf}-c{cargo}-e{int(ele):06d}-u.json"
    bruto = baixa(f"{BASE}/{ele}/dados/{uf}/{nome}", timeout=35)
    if not bruto:
        return None
    try:
        return json.loads(bruto)
    except Exception:
        return None


def foto_candidato(ele: str, uf: str, sq: str) -> Image.Image | None:
    """Fotos ficam em .../<eleicao>/fotos/<uf>/<sqcand>.jpeg (não mudam: cache local)."""
    FOTOS.mkdir(parents=True, exist_ok=True)
    arq = FOTOS / f"{sq}.jpeg"
    if not arq.exists():
        b = baixa(f"{BASE}/{ele}/fotos/{uf}/{sq}.jpeg", timeout=25)
        if not b:
            return None
        arq.write_bytes(b)
    try:
        im = Image.open(arq).convert("RGB")
        return im
    except Exception:
        return None


def candidatos(d: dict, cargo_cd: str) -> list[dict]:
    """Lista ordenada por votos, juntando todos os partidos/coligações."""
    for c in d.get("carg", []):
        if str(c.get("cd")).lstrip("0") != str(cargo_cd).lstrip("0"):
            continue
        lista = []
        for agr in c.get("agr", []):
            for p in agr.get("par", []):
                for cd in p.get("cand", []):
                    v = int(cd.get("vap") or 0)
                    lista.append({
                        "nome": cd.get("nmu") or cd.get("nm") or "?",
                        "partido": p.get("sg") or "",
                        "votos": v,
                        "pct": cd.get("pvap") or "0",
                        "sq": str(cd.get("sqcand") or ""),
                        "situacao": cd.get("st") or "",
                    })
        lista.sort(key=lambda x: -x["votos"])
        return lista
    return []



# ---------------------------------------------------------------- regiões
REGIOES = {
    "Norte": ["ac", "ap", "am", "pa", "ro", "rr", "to"],
    "Nordeste": ["al", "ba", "ce", "ma", "pb", "pe", "pi", "rn", "se"],
    "Centro-Oeste": ["df", "go", "mt", "ms"],
    "Sudeste": ["es", "mg", "rj", "sp"],
    "Sul": ["pr", "sc", "rs"],
}
CORES_REGIAO = {
    "Norte": (46, 134, 88),
    "Nordeste": (235, 110, 30),
    "Centro-Oeste": (200, 168, 40),
    "Sudeste": (40, 96, 175),
    "Sul": (150, 70, 160),
}


def apuracao_por_regiao(ele: str = "6257", cargo: str = "0001") -> dict:
    """% de urnas apuradas por região, somando as UFs de cada uma.

    Consulta o arquivo de Presidente de cada UF (o TSE não publica arquivo por
    região) e soma seções totalizadas / seções totais.
    """
    from concurrent.futures import ThreadPoolExecutor

    todas = [(uf, reg) for reg, ufs in REGIOES.items() for uf in ufs]

    def uma(item):
        uf, reg = item
        d = pega_resultado(ele, uf, cargo)
        if not d:
            return None
        s = d.get("s", {})
        try:
            st = int(s.get("st") or 0)
            ts = int(s.get("ts") or 0)
        except Exception:
            return None
        votos = {}
        nomes = {}
        for c in d.get("carg", []):
            for agr in c.get("agr", []):
                for p in agr.get("par", []):
                    for cd in p.get("cand", []):
                        sq = str(cd.get("sqcand") or "")
                        if not sq:
                            continue
                        votos[sq] = votos.get(sq, 0) + int(cd.get("vap") or 0)
                        nomes[sq] = cd.get("nmu") or cd.get("nm") or ""
        try:
            vv = int((d.get("v", {}) or {}).get("vv") or 0)
        except Exception:
            vv = 0
        return {"uf": uf.upper(), "regiao": reg, "st": st, "ts": ts,
                "votos": votos, "nomes": nomes, "vv": vv}

    with ThreadPoolExecutor(max_workers=6) as ex:
        res = list(ex.map(uma, todas))

    saida = {}
    for r in res:
        if not r:
            continue
        g = saida.setdefault(r["regiao"],
                             {"st": 0, "ts": 0, "ufs": 0, "vv": 0, "votos": {}, "nomes": {}})
        g["st"] += r["st"]
        g["ts"] += r["ts"]
        g["ufs"] += 1
        g["vv"] += r["vv"]
        for sq, v in r["votos"].items():
            g["votos"][sq] = g["votos"].get(sq, 0) + v
            g["nomes"][sq] = r["nomes"].get(sq) or g["nomes"].get(sq, "")
    for reg, g in saida.items():
        g["pct"] = round(100.0 * g["st"] / g["ts"], 2) if g["ts"] else 0.0
    total_st = sum(g["st"] for g in saida.values()) or 1
    for reg, g in saida.items():
        g["fatia"] = round(100.0 * g["st"] / total_st, 1)

    # ---- projeção: cada região mantém o padrão que já apurou, extrapolado
    # para as urnas que ainda faltam naquela região. É o que captura o efeito
    # regional (Nordeste puxando um candidato, Sul/Sudeste puxando outro):
    # regiões mais adiantadas pesam menos no resultado final do que pesam agora.
    proj_votos, proj_validos = {}, 0.0
    for reg, g in saida.items():
        if not g["st"] or not g["ts"]:
            continue
        fator = g["ts"] / g["st"]
        proj_validos += g["vv"] * fator
        for sq, v in g["votos"].items():
            proj_votos[sq] = proj_votos.get(sq, 0.0) + v * fator
    nomes_nac = {}
    for g in saida.values():
        if isinstance(g, dict):
            for sq, nm in g.get("nomes", {}).items():
                if nm:
                    nomes_nac.setdefault(sq, nm)
    projecao = []
    if proj_validos > 0:
        for sq, v in sorted(proj_votos.items(), key=lambda kv: -kv[1])[:2]:
            projecao.append({"sq": sq, "nome": nomes_nac.get(sq, "?"),
                             "pct": round(100.0 * v / proj_validos, 2)})
    saida["_projecao"] = projecao

    # ---- blocos de regiões (pedido do Pedro, 04/10):
    #      1) Sul + Sudeste + Centro-Oeste   2) Nordeste   3) Norte
    # Usa os mesmos números já coletados, sem nova ida à rede. O percentual do
    # bloco é ponderado pelas seções (não é a soma dos percentuais regionais,
    # que daria mais de 100% e não significaria nada).
    blocos_reg = [
        ("Sul + Sudeste + Centro-Oeste", ["Sul", "Sudeste", "Centro-Oeste"]),
        ("Nordeste", ["Nordeste"]),
        ("Norte", ["Norte"]),
    ]
    grupos = []
    for nome_b, regs in blocos_reg:
        st = ts = vv = 0
        votos_b = {}
        for rg in regs:
            g = saida.get(rg)
            if not isinstance(g, dict):
                continue
            st += g["st"]
            ts += g["ts"]
            vv += g["vv"]
            for sq, v in g["votos"].items():
                votos_b[sq] = votos_b.get(sq, 0) + v
        if not ts:
            continue
        cands_b = []
        for sq, v in sorted(votos_b.items(), key=lambda kv: -kv[1])[:2]:
            cands_b.append({
                "nome": nomes_nac.get(sq, "?"),
                "pct": round(100.0 * v / vv, 2) if vv else 0.0,
                "votos": int(v),
            })
        grupos.append({
            "grupo": nome_b,
            "pct": round(100.0 * st / ts, 2),   # seções apuradas no bloco
            "st": st, "ts": ts,
            "cands": cands_b,
        })
    saida["_grupos"] = grupos
    return saida



HIST2022 = Path.home() / ".hermes" / "eleicao" / "historico_2022.json"


def historia_2022() -> list:
    """Pontos de apuração da última eleição presidencial (1º turno, 02/10/2022).

    Fonte oficial: TSE, dataset "Histórico totalização Presidente 1º Turno 2022".
    Cada ponto = [% de seções apuradas, % Lula, % Jair Bolsonaro, horário].
    """
    if not HIST2022.exists():
        return []
    try:
        return json.loads(HIST2022.read_text(encoding="utf-8")).get("pontos", [])
    except Exception:
        return []


def comparacao_2022(pct_agora: float) -> dict | None:
    """Como estava a mesma altura da apuração na última eleição presidencial."""
    pts = historia_2022()
    if not pts or pct_agora is None:
        return None
    m = min(pts, key=lambda r: abs(r[0] - pct_agora))
    return {"pct": m[0], "lula": m[1], "oponente": m[2], "hora": m[3]}


def pct_presidente(bloco: dict, nome: str) -> float | None:
    for c in bloco.get("cands", []):
        if nome.upper() in str(c["nome"]).upper():
            try:
                return float(str(c["pct"]).replace(",", ".").replace("%", ""))
            except Exception:
                return None
    return None


def num(n) -> str:
    try:
        return f"{int(n):,}".replace(",", ".")
    except Exception:
        return str(n)


def monta() -> tuple[Image.Image, list[dict]]:
    blocos = []
    for apelido, ele, uf, cargo, rotulo in CORRIDAS:
        d = pega_resultado(ele, uf, cargo)
        if not d:
            blocos.append({"rotulo": rotulo, "erro": True})
            continue
        s = d.get("s", {})
        e = d.get("e", {})
        v = d.get("v", {})
        cands = candidatos(d, cargo)
        for c in cands[:2]:
            im = foto_candidato(ele, uf, c["sq"])
            c["foto"] = im
        regioes = apuracao_por_regiao(ele, cargo) if uf == "br" else None
        blocos.append({
            "rotulo": rotulo, "erro": False,
            "regioes": regioes,
            "ele": ele, "uf": uf,
            "pct": s.get("pst") or s.get("psnt") or "?",
            "sec_tot": s.get("st"), "sec_total": s.get("ts"),
            "eleitorado": e.get("te"), "comparecimento": e.get("c"),
            "abstencao": e.get("a"), "validos": v.get("vv"),
            "brancos": v.get("vb"), "nulos": v.get("tvn"), "total": v.get("tv"),
            "andamento": d.get("and"), "gerado": f"{d.get('dg')} {d.get('hg')}",
            "cands": cands[:2],
        })
    # comparação com a última eleição presidencial, na mesma altura da apuração
    for b in blocos:
        if not b.get("erro") and b.get("regioes"):
            try:
                agora = float(str(b["pct"]).replace(",", "."))
            except Exception:
                agora = None
            ref = comparacao_2022(agora)
            if ref:
                b["comparacao"] = {
                    "agora_pct": agora,
                    "lula_agora": pct_presidente(b, "LULA"),
                    "oponente_agora": pct_presidente(b, "BOLSONARO"),
                    "oponente_nome": next((c["nome"] for c in b.get("cands", [])
                                           if "BOLSONARO" in str(c["nome"]).upper()), "—"),
                    "ref": ref,
                }
    return blocos


def _cabecalho(img, dr, W, M, ALT_CAB):
    dr.rectangle([0, 0, W, ALT_CAB], fill=AZUL)
    dr.text((M, 24), "ELEIÇÕES 2026 · 1º TURNO", font=fonte(F_BOLD, 40), fill=BRANCO)
    agora = datetime.now().strftime("%d/%m/%Y %H:%M")
    dr.text((M, 76), f"Apuração oficial do TSE · painel gerado às {agora}",
            font=fonte(F_REG, 20), fill=(200, 212, 228))


def _titulo_corrida(dr, b, x0, larg, y, ALT_TITULO, estreita=False):
    dr.rectangle([x0, y, x0 + larg, y + ALT_TITULO], fill=BRANCO)
    dr.rectangle([x0, y, x0 + 8, y + ALT_TITULO], fill=LARANJA)
    dr.text((x0 + 22, y + 18), b["rotulo"], font=fonte(F_BOLD, 28), fill=TINTA)
    if b["erro"]:
        dr.text((x0 + 22, y + 62), "sem dados do TSE nesta rodada",
                font=fonte(F_REG, 19), fill=(180, 60, 60))
        return
    dr.text((x0 + 22, y + 62),
            f"{b['pct']}% apurado  ·  {num(b['sec_tot'])} de {num(b['sec_total'])} seções",
            font=fonte(F_REG, 21), fill=CINZA)
    if not estreita:
        bw = min(300, larg - 60)
        bx, by, bh = x0 + larg - bw - 22, y + 30, 16
        dr.rounded_rectangle([bx, by, bx + bw, by + bh], 8, fill=(226, 230, 236))
        try:
            prop = float(str(b["pct"]).replace(",", ".")) / 100
        except Exception:
            prop = 0
        dr.rounded_rectangle([bx, by, bx + max(6, int(bw * prop)), by + bh], 8, fill=LARANJA)
        dr.text((bx + bw, by + 26), f"gerado {str(b['gerado'])[-8:]}",
                font=fonte(F_REG, 16), fill=CINZA, anchor="ra")


def _linhas_candidato(img, dr, b, x0, larg, y, ALT_CAND, estreita=False):
    for i, c in enumerate(b.get("cands", [])):
        fundo = BRANCO if i % 2 == 0 else (250, 251, 253)
        dr.rectangle([x0, y, x0 + larg, y + ALT_CAND], fill=fundo)
        px, py, pt = x0 + 22, y + 16, 72
        foto = c.get("foto")
        if foto:
            f = foto.copy()
            f.thumbnail((pt, pt))
            img.paste(f, (px + (pt - f.width) // 2, py + (pt - f.height) // 2))
        else:
            dr.rounded_rectangle([px, py, px + pt, py + pt], 8, fill=(214, 219, 226))
        dr.rounded_rectangle([px, py, px + pt, py + pt], 8, outline=(210, 216, 224), width=2)
        tx = px + pt + 20
        pct = str(c["pct"]).replace(".", ",")
        if estreita:
            # coluna de 30%: nome curto em cima, votos embaixo — nada se sobrepõe
            dr.text((tx, y + 12), str(c["nome"])[:17], font=fonte(F_BOLD, 22), fill=TINTA)
            dr.text((tx, y + 42), str(c["partido"])[:14], font=fonte(F_REG, 17), fill=CINZA)
            dr.text((tx, y + 66), f"{num(c['votos'])}  ·  {pct}%",
                    font=fonte(F_BOLD, 19), fill=LARANJA)
            y += ALT_CAND
            continue
        dr.text((tx, y + 18), str(c["nome"])[:26], font=fonte(F_BOLD, 26), fill=TINTA)
        dr.text((tx, y + 54), str(c["partido"])[:16], font=fonte(F_REG, 19), fill=CINZA)
        dr.text((x0 + larg - 132, y + 20), num(c["votos"]), font=fonte(F_BOLD, 25),
                fill=TINTA, anchor="ra")
        dr.text((x0 + larg - 22, y + 20), f"{pct}%", font=fonte(F_BOLD, 26),
                fill=LARANJA, anchor="ra")
        bx2, by2 = tx, y + 82
        bw2 = max(20, x0 + larg - 150 - tx)
        dr.rounded_rectangle([bx2, by2, bx2 + bw2, by2 + 9], 4, fill=(230, 234, 240))
        try:
            pr = float(str(c["pct"]).replace(",", ".")) / 100
        except Exception:
            pr = 0
        cor = LARANJA if i == 0 else (120, 132, 148)
        dr.rounded_rectangle([bx2, by2, bx2 + max(4, int(bw2 * pr)), by2 + 9], 4, fill=cor)
        y += ALT_CAND
    return y


def desenha(blocos: list[dict]) -> Image.Image:
    """Duas colunas: Presidente ocupa 70% da largura, Ceará os 30% restantes."""
    W = 1920
    M = 30
    ALT_CAB = 112
    ALT_TITULO = 126
    ALT_CAND = 100
    ALT_RODAPE = 58
    ALT_REG_TIT = 92
    ALT_REG = 62
    ALT_CMP = 152
    LARG_L = int(W * 0.70)          # Presidente
    LARG_R = W - LARG_L             # Ceará
    MARG = 22

    pres = next((b for b in blocos if b.get("regioes")), None)
    # na coluna direita, os governos (Ceará e São Paulo), empilhados meio a meio
    governos = [b for b in blocos if b.get("uf") in ("ce", "sp")]

    def altura_pres(b):
        if not b or b["erro"]:
            return ALT_TITULO + ALT_CAND
        h = ALT_TITULO + ALT_CAND * len(b.get("cands", []))
        if b.get("regioes"):
            h += ALT_REG_TIT + ALT_REG * 5 + 18
        if b.get("comparacao"):
            h += ALT_CMP
        return h

    def altura_gov(b):
        if not b or b["erro"]:
            return ALT_TITULO + ALT_CAND
        return ALT_TITULO + ALT_CAND * len(b.get("cands", [])) + 214

    hL = altura_pres(pres)
    # os dois governos dividem a coluna meio a meio
    hR = max([altura_gov(g) for g in governos] or [0]) * max(1, len(governos))
    corpo = max(hL, hR)
    img = Image.new("RGB", (W, ALT_CAB + corpo + ALT_RODAPE), FUNDO)
    dr = ImageDraw.Draw(img)

    _cabecalho(img, dr, W, M, ALT_CAB)

    # separador vertical
    dr.rectangle([LARG_L, ALT_CAB, LARG_L + 1, ALT_CAB + corpo], fill=(206, 212, 220))

    # ---------- coluna esquerda: PRESIDENTE (70%)
    y = ALT_CAB
    if pres:
        _titulo_corrida(dr, pres, 0, LARG_L, y, ALT_TITULO)
        y += ALT_TITULO
        if not pres["erro"]:
            y = _linhas_candidato(img, dr, pres, 0, LARG_L, y, ALT_CAND)

            if pres.get("regioes"):
                dr.rectangle([0, y, LARG_L, y + ALT_REG_TIT], fill=BRANCO)
                dr.line([M, y, LARG_L - M, y], fill=(230, 234, 240), width=2)
                dr.text((M, y + 16), "URNAS APURADAS POR REGIÃO", font=fonte(F_BOLD, 22), fill=AZUL)
                dr.text((M, y + 48), "barra cheia = seções já apuradas  ·  parte clara = o que falta",
                        font=fonte(F_REG, 17), fill=CINZA)
                y += ALT_REG_TIT
                dados = sorted([(k, g) for k, g in pres["regioes"].items() if not k.startswith("_")],
                               key=lambda kv: -kv[1]["pct"])
                bx, bw = M + 230, LARG_L - M - 230 - 190
                for i, (nome, g) in enumerate(dados):
                    ry = y + i * ALT_REG
                    cor = CORES_REGIAO.get(nome, (140, 140, 140))
                    dr.text((M, ry + 2), nome, font=fonte(F_BOLD, 22), fill=TINTA)
                    dr.text((M, ry + 28), f"{num(g['st'])} de {num(g['ts'])} seções",
                            font=fonte(F_REG, 15), fill=CINZA)
                    dr.rounded_rectangle([bx, ry, bx + bw, ry + 32], 8, fill=(229, 233, 240))
                    prop = max(0.0, min(1.0, g["pct"] / 100.0))
                    dr.rounded_rectangle([bx, ry, bx + max(6, int(bw * prop)), ry + 32], 8, fill=cor)
                    dr.text((bx + bw + 16, ry - 2), f"{g['pct']:.2f}%", font=fonte(F_BOLD, 25), fill=cor)
                    dr.text((bx + bw + 16, ry + 28), f"falta {max(0.0, 100.0 - g['pct']):.2f}%",
                            font=fonte(F_REG, 16), fill=CINZA)
                y += ALT_REG * len(dados) + 18

            cmp_ = pres.get("comparacao")
            if cmp_:
                ref = cmp_["ref"]
                dr.rectangle([0, y, LARG_L, y + ALT_CMP], fill=(245, 247, 250))
                dr.rectangle([M, y + 14, M + 6, y + ALT_CMP - 14], fill=(60, 72, 90))
                dr.text((M + 20, y + 14),
                        "ÚLTIMA ELEIÇÃO PRESIDENCIAL (2022) — na mesma altura da apuração",
                        font=fonte(F_BOLD, 21), fill=AZUL)
                la, oa = cmp_.get("lula_agora"), cmp_.get("oponente_agora")
                op_nome = str(cmp_.get("oponente_nome") or "—").split()[-1].upper()
                dr.text((M + 20, y + 46), "2026 · agora", font=fonte(F_REG, 17), fill=CINZA)
                dr.text((M + 20, y + 96), f"2022 · {ref['pct']:.2f}% apurado",
                        font=fonte(F_REG, 17), fill=CINZA)
                dr.text((M + 250, y + 44), "LULA", font=fonte(F_BOLD, 18), fill=CINZA)
                dr.text((M + 480, y + 44), op_nome, font=fonte(F_BOLD, 18), fill=CINZA)
                dr.text((M + 250, y + 66), f"{la:.2f}%" if la else "—", font=fonte(F_BOLD, 30), fill=TINTA)
                dr.text((M + 480, y + 66), f"{oa:.2f}%" if oa else "—", font=fonte(F_BOLD, 30), fill=TINTA)
                dr.text((M + 250, y + 104), f"{ref['lula']:.2f}%", font=fonte(F_BOLD, 30), fill=(90, 100, 115))
                dr.text((M + 480, y + 104), f"{ref['oponente']:.2f}%",
                        font=fonte(F_BOLD, 30), fill=(90, 100, 115))
                dr.text((M + 700, y + 104), f"às {ref['hora']}", font=fonte(F_REG, 16), fill=CINZA)
                dr.text((M + 700, y + 46), "dados oficiais do TSE", font=fonte(F_REG, 16), fill=CINZA)
                y += ALT_CMP

    # ---------- coluna direita: GOVERNOS (30%) — Ceará e São Paulo, meio a meio
    y2 = ALT_CAB
    for gov in governos:
        _titulo_corrida(dr, gov, LARG_L + MARG, LARG_R - MARG * 2, y2, ALT_TITULO, estreita=True)
        y2 += ALT_TITULO
        if gov["erro"]:
            continue
        y2 = _linhas_candidato(img, dr, gov, LARG_L + MARG, LARG_R - MARG * 2, y2,
                               ALT_CAND, estreita=True)
        dr.rectangle([LARG_L + MARG, y2 + 8, W - MARG, y2 + 190], fill=BRANCO)
        dr.rectangle([LARG_L + MARG, y2 + 8, LARG_L + MARG + 5, y2 + 190], fill=AZUL)
        dr.text((LARG_L + MARG + 18, y2 + 20), "NÚMEROS DO ESTADO",
                font=fonte(F_BOLD, 19), fill=AZUL)
        linhas = [
            ("Seções apuradas", f"{num(gov['sec_tot'])} de {num(gov['sec_total'])}"),
            ("Eleitorado", num(gov.get("eleitorado"))),
            ("Comparecimento", num(gov.get("comparecimento"))),
            ("Votos válidos", num(gov.get("validos"))),
            ("Brancos / nulos", f"{num(gov.get('brancos'))} / {num(gov.get('nulos'))}"),
        ]
        ly = y2 + 50
        for rot, val in linhas:
            dr.text((LARG_L + MARG + 18, ly), rot, font=fonte(F_REG, 17), fill=CINZA)
            dr.text((W - MARG - 18, ly), val, font=fonte(F_BOLD, 17), fill=TINTA, anchor="ra")
            ly += 27
        y2 += 214

    # ---------- rodapé
    yr = ALT_CAB + corpo
    dr.rectangle([0, yr, W, yr + ALT_RODAPE], fill=(238, 241, 245))
    dr.text((M, yr + 12),
            "Fonte: TSE · resultados.tse.jus.br/oficial/ele2026 · comparação com o histórico oficial "
            "da totalização de 2022 (Portal de Dados Abertos do TSE)",
            font=fonte(F_REG, 17), fill=CINZA)
    dr.text((M, yr + 34),
            "Painel em duas colunas: Presidente com 70% da largura, Governador do Ceará com 30%.",
            font=fonte(F_REG, 17), fill=CINZA)
    return img




def envia(img: Image.Image, legenda: str) -> bool:
    """Igual ao alerta do portão: só o Telegram do Pedro."""
    tok = _envkey("TELEGRAM_BOT_TOKEN")
    chat = _envkey("TELEGRAM_HOME_CHANNEL") or "7860888290"
    if not tok:
        print("  sem TELEGRAM_BOT_TOKEN")
        return False
    RAIZ.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    SAIDA.write_bytes(buf.getvalue())
    r = subprocess.run(
        ["curl", "-s", "-X", "POST", f"https://api.telegram.org/bot{tok}/sendPhoto",
         "-F", f"chat_id={chat}", "-F", f"photo=@{SAIDA}",
         "-F", f"caption={legenda}"],
        capture_output=True, text=True, timeout=90)
    ok = '"ok":true' in (r.stdout or "")
    print("  telegram: enviado" if ok else f"  falhou: {(r.stdout or '')[:160]}")
    return ok


def _envkey(nome: str) -> str:
    """Lê de ~/.hermes/.env (mesmo arquivo que o resto do assistente usa)."""
    import os
    v = os.environ.get(nome)
    if v:
        return v.strip()
    env = Path.home() / ".hermes" / ".env"
    if env.exists():
        for linha in env.read_text(encoding="utf-8", errors="ignore").splitlines():
            linha = linha.strip()
            if linha.startswith("#") or "=" not in linha:
                continue
            k, _, val = linha.partition("=")
            if k.strip() == nome:
                return val.strip().strip('"').strip("'")
    return ""


def main() -> int:
    if datetime.now().strftime("%Y-%m-%d") != DIA:
        print(f"fora do dia da eleição ({DIA}) — nada a fazer")
        return 0
    blocos = monta()
    img = desenha(blocos)
    resumo = []
    for b in blocos:
        if b["erro"]:
            resumo.append(f"{b['rotulo']}: sem dados")
        else:
            top = b["cands"][0] if b["cands"] else None
            resumo.append(f"{b['rotulo']}: {b['pct']}% apurado" +
                          (f" · {top['nome']} {top['pct']}%" if top else ""))
    legenda = "🗳️ Apuração TSE — " + datetime.now().strftime("%H:%M") + "\n" + "\n".join(resumo)
    envia(img, legenda)
    print(f"  imagem: {SAIDA} ({SAIDA.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
