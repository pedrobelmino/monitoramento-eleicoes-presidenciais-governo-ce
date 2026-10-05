#!/usr/bin/env python3
"""Mapa do Brasil com a projeção dos governadores.

Para cada estado: quem lidera a corrida de governador, se passaria de 50% (o
que significa vitória já no 1º turno) e o lado político do partido — vermelho
para esquerda, verde para direita. Estados onde ninguém passou de 50% ficam em
cinza (segundo turno).

Uso:  python mapa_governadores.py [saida.svg]
"""
from __future__ import annotations

import base64
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SVG_BASE = Path.home() / ".hermes" / "eleicao" / "br.svg"

UFS = ["ac", "al", "ap", "am", "ba", "ce", "df", "es", "go", "ma", "mt", "ms",
       "mg", "pa", "pb", "pr", "pe", "pi", "rj", "rn", "rs", "ro", "rr", "sc",
       "sp", "se", "to"]

# Nome do grupo no SVG original -> código da UF
ESTADO_UF = {
    "Rondonia": "RO", "Roraima": "RR", "Acre": "AC", "Amazonas": "AM",
    "Amapa": "AP", "Tocantins": "TO", "MatoGrosso": "MT", "Goias": "GO",
    "MatoGrosso_do_Sul": "MS", "MinasGerais": "MG", "Parana": "PR",
    "RioGrand_do_Sul": "RS", "Bahina": "BA", "Piaui": "PI", "Ceara": "CE",
    "Alagoas": "AL", "Sergipe": "SE", "BrasiliaDistritoFederal": "DF",
    "Pernambuco": "PE", "Maranhao": "MA", "Para": "PA", "SaoPaulo": "SP",
    "Rio_deJaneiro": "RJ", "EspiritoSanto": "ES", "SantaCatarina": "SC",
    "Paraiba": "PB",
}
NOME_UF = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas",
    "BA": "Bahia", "CE": "Ceará", "DF": "Distrito Federal", "ES": "Espírito Santo",
    "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso", "MS": "Mato Grosso do Sul",
    "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
    "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima",
    "SC": "Santa Catarina", "SP": "São Paulo", "SE": "Sergipe", "TO": "Tocantins",
}

# Região de cada UF — usada para agrupar a lista ao lado do mapa.
REGIAO = {
    "Norte": ["AC", "AP", "AM", "PA", "RO", "RR", "TO"],
    "Nordeste": ["AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"],
    "Centro-Oeste": ["DF", "GO", "MT", "MS"],
    "Sudeste": ["ES", "MG", "RJ", "SP"],
    "Sul": ["PR", "RS", "SC"],
}
UF_REGIAO = {uf: reg for reg, ufs in REGIAO.items() for uf in ufs}

# Classificação dos partidos por lado. É uma leitura política, não um dado
# oficial do TSE — os dois lados são declarados aqui de forma explícita.
ESQUERDA = {"PT", "PSB", "PDT", "PCDOB", "PC DO B", "PV", "PSOL", "REDE",
            "PSTU", "PCO", "UP", "PCB", "PSOL/REDE"}
DIREITA = {"PL", "PP", "REPUBLICANOS", "UNIÃO", "UNIAO", "NOVO", "PSD", "MDB",
           "PSDB", "CIDADANIA", "PODE", "PODEMOS", "PRD", "AVANTE", "DC",
           "MISSÃO", "MISSAO", "DEMOCRATA", "SOLIDARIEDADE", "PSL", "DEM",
           "PRTB", "PMB", "PSC", "PATRIOTA", "PROS", "PTB", "PHS", "PPL"}

COR_ESQUERDA = "#d13438"        # vermelho cheio: esquerda, já eleito no 1º turno
COR_DIREITA = "#2ea043"         # verde cheio: direita, já eleito no 1º turno
COR_ESQUERDA_2T = "#f0a2a5"     # vermelho claro: esquerda, vai ao 2º turno
COR_DIREITA_2T = "#9fd8ae"      # verde claro: direita, vai ao 2º turno
COR_NEUTRO = "#8b949e"          # cinza: sem dados


def lado_do_partido(partido: str) -> str:
    p = (partido or "").strip().upper()
    if p in ESQUERDA:
        return "esquerda"
    if p in DIREITA:
        return "direita"
    for sigla in ESQUERDA:
        if sigla and sigla in p:
            return "esquerda"
    for sigla in DIREITA:
        if sigla and sigla in p:
            return "direita"
    return "nao_classificado"


def busca_governadores() -> list[dict]:
    """Corrida de governador em cada UF, com o líder e o lado do partido."""
    sys.path.insert(0, str(Path(__file__).parent))
    import painel_eleicao as pe

    def uma(uf: str) -> dict | None:
        d = pe.pega_resultado("6259", uf, "0003")
        if not d:
            return None
        cs = pe.candidatos(d, "0003")
        if not cs:
            return None
        s = d.get("s", {}) or {}
        c = cs[0]
        try:
            pct = float(str(c.get("pct", "0")).replace(",", "."))
        except Exception:
            pct = 0.0
        lado = lado_do_partido(c.get("partido", ""))
        try:                       # a foto não vem no dicionário: é buscada à parte
            foto = pe.foto_candidato("6259", uf, c.get("sq"))
        except Exception:
            foto = None
        return {
            "foto": foto,
            "uf": uf.upper(),
            "estado": NOME_UF.get(uf.upper(), uf.upper()),
            "nome": str(c.get("nome", "")).title(),
            "partido": str(c.get("partido", "")),
            "pct": pct,
            "lado": lado,
            "regiao": UF_REGIAO.get(uf.upper(), "—"),
            "vence_1t": pct > 50.0,
            "cor": (COR_ESQUERDA if (lado == "esquerda" and pct > 50.0)
                    else COR_ESQUERDA_2T if lado == "esquerda"
                    else COR_DIREITA if (lado == "direita" and pct > 50.0)
                    else COR_DIREITA_2T if lado == "direita"
                    else COR_NEUTRO),
            "apurado": s.get("pst", "0"),
        }

    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(uma, UFS))
    return [r for r in res if r]


def _enxuga(svg: str) -> str:
    """Diminui muito o arquivo: coordenadas inteiras e escala 1/10.

    O desenho é o mesmo — o viewBox encolhe junto, então a proporção e a
    posição relativa de cada estado não mudam. Sem isso o SVG passa de 2 MB.
    """
    def dentro_d(m):
        corpo = re.sub(r"-?\d+(?:\.\d+)?", lambda x: str(int(float(x.group(0)) // 10)),
                       m.group(1))
        return 'd="' + corpo + '"'
    svg = re.sub(r'd="([^"]+)"', dentro_d, svg)
    # o stroke-width do arquivo original está na escala antiga: sem isto a divisa
    # entre os estados fica 10x mais grossa depois do encolhimento
    svg = re.sub(r"stroke-width:282\.[0-9]+", "stroke-width:16", svg)
    return svg.replace('viewBox="0 0 220000 194010"', 'viewBox="0 0 22000 19401"')


def _centro_do_estado(corpo: str) -> tuple[float, float] | None:
    """Centro aproximado da caixa do desenho do estado, em coordenadas do SVG."""
    # só o atributo d="..." interessa: cores (#d13438) e títulos também têm
    # dígitos e jogariam o centro para fora do mapa
    pontos: list[tuple[float, float]] = []
    for d in re.findall(r'd="([^"]+)"', corpo):
        nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", d)]
        pontos += list(zip(nums[0::2], nums[1::2]))
    if len(pontos) < 4:
        return None
    xs = [p[0] for p in pontos]
    ys = [p[1] for p in pontos]
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def _marcadores(svg: str, governadores: list[dict]) -> str:
    """Sobre o mapa, a foto, o nome e o percentual de quem já passou de 50%.

    Só entram os estados com vitória projetada no 1º turno — é o "possivelmente
    eleito". O marcador é ancorado no centro da caixa do desenho do estado.
    """
    from PIL import Image

    partes = []
    defs = []
    for i, g in enumerate(sorted(governadores, key=lambda x: -x["pct"])):
        if not g["vence_1t"]:
            continue
        m = re.search(r'<g\s+id="([^"]+)"(.*?)</g>', svg, re.S)
        # acha o grupo do estado pelo código da UF
        alvo = None
        for nome_estado, uf in ESTADO_UF.items():
            if uf == g["uf"]:
                alvo = nome_estado
                break
        if not alvo:
            continue
        m = re.search(r'<g\s+id="' + re.escape(alvo) + r'"[^>]*>(.*?)</g>', svg, re.S)
        if not m:
            continue
        centro = _centro_do_estado(m.group(1))
        if not centro:
            continue
        cx, cy = centro
        pid = f"c{i}"

        foto = ""
        img = g.get("foto")
        if img is not None:
            try:
                im = img.convert("RGB").copy()
                im.thumbnail((220, 220), Image.LANCZOS)
                lado = min(im.size)
                im = im.crop(((im.width - lado) // 2, (im.height - lado) // 2,
                              (im.width + lado) // 2, (im.height + lado) // 2))
                im = im.resize((128, 128))
                import io as _io
                b = _io.BytesIO()
                im.save(b, "JPEG", quality=78)
                b64 = base64.b64encode(b.getvalue()).decode()
                defs.append(f'<clipPath id="{pid}"><circle cx="0" cy="0" r="620"/></clipPath>')
                foto = (f'<image x="-620" y="-620" width="1240" height="1240" '
                        f'clip-path="url(#{pid})" preserveAspectRatio="xMidYMid slice" '
                        f'href="data:image/jpeg;base64,{b64}"/>')
            except Exception:
                foto = ""
        if not foto:
            foto = (f'<circle cx="0" cy="0" r="430" fill="{g["cor"]}" '
                    f'stroke="#ffffff" stroke-width="30"/>')

        nome = g["nome"].split()[0].upper()[:14]
        partes.append(
            f'<g transform="translate({cx:.0f},{cy:.0f})">'
            f'{foto}'
            f'<circle cx="0" cy="0" r="630" fill="none" stroke="#ffffff" stroke-width="34" opacity="0.95"/>'
            # só o percentual: o nome completo fica na lista ao lado e no hover
            f'<text y="880" text-anchor="middle" font-family="Arial,Helvetica,sans-serif" '
            f'font-size="500" font-weight="bold" fill="{g["cor"]}" stroke="#0d1117" '
            f'stroke-width="90" paint-order="stroke">{g["pct"]:.1f}%</text>'
            f'</g>')
    if not partes:
        return svg
    bloco = "<defs>" + "".join(defs) + "</defs>" + "".join(partes)
    return svg.replace("</svg>", bloco + "</svg>") if "</svg>" in svg else svg + bloco


def gera_svg(governadores: list[dict], enxugar: bool = True) -> str:
    """Devolve o SVG do Brasil com cada estado na cor do lado vencedor."""
    if not SVG_BASE.exists():
        return ""
    svg = SVG_BASE.read_text(encoding="utf-8", errors="ignore")
    cores = {g["uf"]: g["cor"] for g in governadores}

    info = {g["uf"]: g for g in governadores}

    def troca(m: re.Match) -> str:
        grupo, corpo = m.group(1), m.group(2)
        uf = ESTADO_UF.get(grupo)
        g = info.get(uf) if uf else None
        cor = g["cor"] if g else COR_NEUTRO
        corpo = corpo.replace("fill:#999999", f"fill:{cor}", 1)
        if g:
            titulo = (f'{g["estado"]} — {g["nome"]} ({g["partido"]}) '
                      f'{g["pct"]:.2f}%'
                      + (" · eleito no 1º turno" if g["vence_1t"] else " · vai ao 2º turno"))
        else:
            titulo = NOME_UF.get(uf or "", grupo)
        return f'<g id="{grupo}"><title>{titulo}</title>{corpo.lstrip(">")}'

    svg = re.sub(r'<g\s+id="([^"]+)"(.*?)(?=<g\s+id=|\Z)', troca, svg, flags=re.S)
    # quem manda no tamanho é o CSS (.mapa{width:100%;height:auto}); o viewBox
    # do arquivo define a proporção. width/height fixos atrapalham a escala.
    svg = svg.replace('width="220mm"', '').replace('height="194mm"', '')
    svg = svg.replace('id="Brazil_states"', 'id="Brazil_states" preserveAspectRatio="xMidYMid meet"')
    if enxugar:
        svg = _enxuga(svg)
    svg = _marcadores(svg, governadores)   # foto + nome + % sobre quem já passou de 50%
    return svg


def resumo(governadores: list[dict]) -> dict:
    """Contagens para a legenda."""
    return {
        "esquerda": sum(1 for g in governadores if g["lado"] == "esquerda" and g["vence_1t"]),
        "direita": sum(1 for g in governadores if g["lado"] == "direita" and g["vence_1t"]),
        "segundo_turno": sum(1 for g in governadores if not g["vence_1t"]),
        "esquerda_total": sum(1 for g in governadores if g["lado"] == "esquerda"),
        "direita_total": sum(1 for g in governadores if g["lado"] == "direita"),
    }


if __name__ == "__main__":
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        Path.home() / ".hermes" / "eleicao" / "mapa-governadores.svg")
    govs = busca_governadores()
    svg = gera_svg(govs)
    destino.write_text(svg, encoding="utf-8")
    r = resumo(govs)
    print(f"  estados com dados: {len(govs)}")
    print(f"  vencendo no 1º turno (>50%): esquerda {r['esquerda']} · direita {r['direita']}")
    print(f"  indefinidos (2º turno): {r['segundo_turno']}")
    print(f"  arquivo: {destino} ({destino.stat().st_size} bytes)")
