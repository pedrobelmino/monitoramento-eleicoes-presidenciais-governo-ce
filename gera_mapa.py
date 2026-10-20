"""Gera os contornos das 27 UFs para o mapa do painel.

Lê o GeoJSON público (``properties.sigla``), projeta em coordenadas de tela
(equirretangular com correção de ``cos(lat)``, Y invertido), simplifica cada
anel com Douglas-Peucker e grava ``dados/mapa_ufs.json``. O centroide é o da
maior parte de cada UF, usado para posicionar o rótulo da sigla.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

LARGURA = 1000.0
ALTURA = 1030.0
MARGEM = 8.0
TOLERANCIA = 0.5  # unidade do espaço final (viewBox)
AREA_MINIMA = 1.0  # descarta ilhas minúsculas, em unidades² do viewBox

BASE = Path(__file__).resolve().parent
GEOJSON_PADRAO = (
    BASE / "dados" / "br_states.geojson"
    if (BASE / "dados" / "br_states.geojson").exists()
    else BASE / "amostra" / "br_states.geojson"
)
SAIDA_PADRAO = BASE / "dados" / "mapa_ufs.json"


def _aneis(feature: dict) -> list[list[list[list[float]]]]:
    """Normaliza Polygon/MultiPolygon para uma lista de polígonos (anéis)."""
    geometria = feature.get("geometry") or {}
    tipo = geometria.get("type")
    coordenadas = geometria.get("coordinates") or []
    if tipo == "Polygon":
        return [coordenadas]
    if tipo == "MultiPolygon":
        return coordenadas
    return []


def _area(pontos: list[tuple[float, float]]) -> float:
    """Área com sinal pelo método do sapato (shoelace)."""
    if len(pontos) < 3:
        return 0.0
    soma = 0.0
    for i, (x0, y0) in enumerate(pontos):
        x1, y1 = pontos[(i + 1) % len(pontos)]
        soma += x0 * y1 - x1 * y0
    return soma / 2.0


def _centroide(pontos: list[tuple[float, float]]) -> tuple[float, float]:
    """Centroide de polígono; cai na média se a área for desprezível."""
    if pontos and pontos[0] == pontos[-1]:
        pontos = pontos[:-1]
    if len(pontos) < 3:
        if not pontos:
            return 0.0, 0.0
        return (
            sum(p[0] for p in pontos) / len(pontos),
            sum(p[1] for p in pontos) / len(pontos),
        )

    area = 0.0
    cx = 0.0
    cy = 0.0
    for i, (x0, y0) in enumerate(pontos):
        x1, y1 = pontos[(i + 1) % len(pontos)]
        cruzado = x0 * y1 - x1 * y0
        area += cruzado
        cx += (x0 + x1) * cruzado
        cy += (y0 + y1) * cruzado
    area *= 0.5

    if abs(area) < 1e-9:
        return (
            sum(p[0] for p in pontos) / len(pontos),
            sum(p[1] for p in pontos) / len(pontos),
        )
    return cx / (6.0 * area), cy / (6.0 * area)


def _douglas_peucker(
    pontos: list[tuple[float, float]], tolerancia: float
) -> list[tuple[float, float]]:
    """Simplificação iterativa (sem recursão) com distância ao segmento."""
    total = len(pontos)
    if total <= 2:
        return pontos[:]

    manter = [False] * total
    manter[0] = True
    manter[-1] = True
    pilha = [(0, total - 1)]
    limite = tolerancia * tolerancia

    while pilha:
        inicio, fim = pilha.pop()
        if fim <= inicio + 1:
            continue

        ax, ay = pontos[inicio]
        bx, by = pontos[fim]
        dx = bx - ax
        dy = by - ay
        denominador = dx * dx + dy * dy
        maior = -1.0
        indice = -1

        for i in range(inicio + 1, fim):
            px, py = pontos[i]
            if denominador == 0.0:
                distancia = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / denominador
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                qx = ax + t * dx
                qy = ay + t * dy
                distancia = (px - qx) ** 2 + (py - qy) ** 2
            if distancia > maior:
                maior = distancia
                indice = i

        if indice >= 0 and maior > limite:
            manter[indice] = True
            pilha.append((inicio, indice))
            pilha.append((indice, fim))

    return [ponto for ponto, fica in zip(pontos, manter) if fica]


def _simplifica_anel(
    anel: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Simplifica um anel fechado, mantendo a primeira coordenada no fim."""
    if len(anel) < 4:
        return []
    pontos = anel[:-1] if anel[0] == anel[-1] else anel[:]
    if len(pontos) < 3 or abs(_area(pontos)) < AREA_MINIMA:
        return []

    simplificado = _douglas_peucker(pontos, TOLERANCIA)
    if len(simplificado) < 3 or abs(_area(simplificado)) < AREA_MINIMA:
        return []
    return simplificado + [simplificado[0]]


def _coordenada(ponto: tuple[float, float]) -> str:
    x = 0.0 if abs(ponto[0]) < 0.05 else ponto[0]
    y = 0.0 if abs(ponto[1]) < 0.05 else ponto[1]
    return f"{x:.1f},{y:.1f}"


def _path(anel: list[tuple[float, float]]) -> str:
    return "M " + " L ".join(_coordenada(p) for p in anel) + " Z"


def _projetor(features: list[dict]):
    """Cria a função de projeção ajustada ao viewBox."""
    lons: list[float] = []
    lats: list[float] = []
    for feature in features:
        for poligono in _aneis(feature):
            for anel in poligono:
                for lon, lat in anel:
                    lons.append(float(lon))
                    lats.append(float(lat))

    if not lons:
        raise ValueError("GeoJSON sem coordenadas")

    lon_min, lon_max = min(lons), max(lons)
    lat_min, lat_max = min(lats), max(lats)
    lat_media = (lat_min + lat_max) / 2.0
    fator_lon = math.cos(math.radians(lat_media))

    largura_bruta = (lon_max - lon_min) * fator_lon
    altura_bruta = lat_max - lat_min
    if largura_bruta <= 0.0 or altura_bruta <= 0.0:
        raise ValueError("extensão geográfica inválida")

    util_largura = LARGURA - 2.0 * MARGEM
    util_altura = ALTURA - 2.0 * MARGEM
    escala = min(util_largura / largura_bruta, util_altura / altura_bruta)
    sobra_x = (util_largura - largura_bruta * escala) / 2.0
    sobra_y = (util_altura - altura_bruta * escala) / 2.0

    def projeta(lon: float, lat: float) -> tuple[float, float]:
        x = MARGEM + sobra_x + (float(lon) - lon_min) * fator_lon * escala
        y = MARGEM + sobra_y + (lat_max - float(lat)) * escala
        return x, y

    return projeta


def gera_mapa(caminho_geojson: Path, caminho_saida: Path) -> dict:
    """Lê o GeoJSON, projeta/simplifica e grava o JSON do mapa."""
    with Path(caminho_geojson).open("r", encoding="utf-8") as arquivo:
        geojson = json.load(arquivo)

    features = geojson.get("features") or []
    if not features:
        raise ValueError("GeoJSON sem features")

    projeta = _projetor(features)

    ufs: dict[str, dict] = {}
    total_pontos = 0
    ignoradas: list[str] = []

    for feature in features:
        propriedades = feature.get("properties") or {}
        sigla = str(propriedades.get("sigla") or "").strip().upper()
        if not sigla:
            ignoradas.append("feature sem sigla")
            continue

        caminhos: list[str] = []
        maior_area = 0.0
        maior_anel: list[tuple[float, float]] = []

        for poligono in _aneis(feature):
            if not poligono:
                continue
            externo = [
                projeta(lon, lat) for lon, lat in poligono[0]
            ]
            area_externa = abs(_area(externo))
            if area_externa > maior_area:
                maior_area = area_externa
                maior_anel = externo

            if area_externa < AREA_MINIMA:
                continue

            # Primeiro o contorno externo; depois os buracos, se relevantes.
            for indice, anel in enumerate(poligono):
                if indice == 0:
                    projetado = externo
                else:
                    projetado = [projeta(lon, lat) for lon, lat in anel]
                    if abs(_area(projetado)) < AREA_MINIMA:
                        continue

                simplificado = _simplifica_anel(projetado)
                if not simplificado:
                    continue
                caminhos.append(_path(simplificado))
                total_pontos += len(simplificado)

        if not maior_anel:
            ignoradas.append(sigla)
            continue

        cx, cy = _centroide(maior_anel)
        ufs[sigla] = {
            "d": " ".join(caminhos),
            "cx": round(cx, 1),
            "cy": round(cy, 1),
        }

    if ignoradas:
        raise ValueError("features sem geometria válida: " + ", ".join(ignoradas))

    mapa = {"viewBox": "0 0 1000 1030", "ufs": ufs}

    caminho_saida = Path(caminho_saida)
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    with caminho_saida.open("w", encoding="utf-8") as arquivo:
        json.dump(mapa, arquivo, ensure_ascii=False, separators=(",", ":"))
        arquivo.write("\n")

    return {
        "ufs": len(ufs),
        "pontos": total_pontos,
        "bytes": caminho_saida.stat().st_size,
        "mapa": mapa,
    }


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gera dados/mapa_ufs.json a partir do GeoJSON público."
    )
    parser.add_argument(
        "--geojson",
        default=str(GEOJSON_PADRAO),
        help=f"GeoJSON de entrada (padrão: {GEOJSON_PADRAO})",
    )
    parser.add_argument(
        "--saida",
        default=str(SAIDA_PADRAO),
        help=f"JSON de saída (padrão: {SAIDA_PADRAO})",
    )
    args = parser.parse_args(argv)

    resultado = gera_mapa(Path(args.geojson), Path(args.saida))
    tamanho_kb = resultado["bytes"] / 1024.0
    print(
        f"mapa: {resultado['ufs']} UFs · {resultado['pontos']} pontos · "
        f"{tamanho_kb:.1f} KB · viewBox {resultado['mapa']['viewBox']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
