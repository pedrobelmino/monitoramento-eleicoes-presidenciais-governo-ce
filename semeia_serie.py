#!/usr/bin/env python3
"""Semeia a série histórica de 2022 a partir do CSV oficial do TSE.

O arquivo de entrada é o ``Historico_Totalizacao_Presidente_BR_1T_2022.csv``:
separador ``;``, cabeçalho com espaços no fim e uma linha por timestamp real da
totalização. Aqui não há interpolação, chute nem arredondamento de voto: cada
linha do CSV vira um ponto do JSONL, com os votos acumulados exatamente como o
TSE publicou.

``vv`` é a soma dos votos válidos dos candidatos (todas as colunas
``*_QT_VOTOS_TOT_ACUMULADO``, menos BRANCO e NULO). O bloco ``candidatos`` traz
os dois primeiros colocados de 2022 — LULA (PT) e JAIR BOLSONARO (PL) — com o
percentual calculado sobre esses votos válidos, que é a regra do TSE.

Uso::

    python3 semeia_serie.py
    python3 semeia_serie.py --csv /caminho/arquivo.csv --saida /caminho/serie.jsonl
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

CSV_PADRAO = Path.home() / ".hermes" / "eleicao" / (
    "Historico_Totalizacao_Presidente_BR_1T_2022.csv"
)
SAIDA_PADRAO = Path.home() / ".hermes" / "eleicao_presidente" / (
    "serie_historica_2022.jsonl"
)

# Os dois candidatos que a tela acompanha. O nome da coluna no CSV é a chave;
# o nome exibido e o partido são os oficiais da eleição de 2022.
CANDIDATOS = (
    ("LULA", "LULA", "PT"),
    ("JAIR_BOLSONARO", "JAIR BOLSONARO", "PL"),
)

SUFIXO = "_QT_VOTOS_TOT_ACUMULADO"
NAO_CANDIDATOS = ("BRANCO_", "NULO_")


def _inteiro(valor) -> int:
    """Converte o campo do TSE (com espaços) em inteiro; vazio vira 0."""
    texto = "" if valor is None else str(valor).strip()
    if not texto:
        return 0
    # O histórico usa inteiros separados por ponto de milhar em algumas versões.
    texto = texto.replace(".", "")
    return int(texto)


def _colunas_de_votos(cabecalho: list[str]) -> list[str]:
    """Colunas acumuladas de candidato, sem branco/nulo."""
    colunas = []
    for nome in cabecalho:
        if not nome.endswith(SUFIXO):
            continue
        if nome.startswith(NAO_CANDIDATOS):
            continue
        colunas.append(nome)
    return colunas


def _le(caminho: Path) -> tuple[list[dict], list[str]]:
    if not caminho.exists():
        raise FileNotFoundError(f"CSV não encontrado: {caminho}")

    por_em: dict[str, dict] = {}
    colunas_votos: list[str] = []
    with caminho.open("r", encoding="utf-8-sig", newline="") as arquivo:
        leitor = csv.DictReader(arquivo, delimiter=";")
        if not leitor.fieldnames:
            raise ValueError("CSV sem cabeçalho")
        cabecalho = [nome.strip() for nome in leitor.fieldnames]
        leitor.fieldnames = cabecalho
        colunas_votos = _colunas_de_votos(cabecalho)

        for linha in leitor:
            bruto = (linha.get("DT_TOTALIZACAO") or "").strip()
            if not bruto:
                continue
            try:
                momento = datetime.strptime(bruto, "%d/%m/%Y %H:%M:%S")
            except ValueError:
                continue

            secoes_total = _inteiro(linha.get("QT_SECOES_TOTAL"))
            secoes_acum = _inteiro(linha.get("QT_SECOES_TOT_ACUMULADO"))
            pct_apurado = (
                round(100.0 * secoes_acum / secoes_total, 4)
                if secoes_total
                else None
            )

            votos = {coluna: _inteiro(linha.get(coluna)) for coluna in colunas_votos}
            vv = sum(votos.values())

            candidatos = []
            for chave, nome, partido in CANDIDATOS:
                coluna = f"{chave}{SUFIXO}"
                if coluna not in votos:
                    continue
                acumulado = votos[coluna]
                candidatos.append({
                    "nome": nome,
                    "partido": partido,
                    "votos": acumulado,
                    "pct": round(100.0 * acumulado / vv, 4) if vv else 0.0,
                })

            em = momento.isoformat()
            por_em[em] = {
                "eleicao": "2022",
                "turno": "1º turno (referência)",
                "origem": "arquivo_historico_tse",
                "fonte": "TSE - Histórico Totalização Presidente 1º Turno 2022",
                "em": em,
                "pct_apurado": pct_apurado,
                "vv": vv,
                "candidatos": candidatos,
            }

    return list(por_em.values()), colunas_votos


def semeia(caminho_csv: Path, saida: Path) -> list[dict]:
    pontos, _ = _le(caminho_csv)
    if not pontos:
        raise ValueError("nenhum ponto válido encontrado no CSV")
    saida.parent.mkdir(parents=True, exist_ok=True)
    with saida.open("w", encoding="utf-8") as arquivo:
        for ponto in pontos:
            arquivo.write(json.dumps(ponto, ensure_ascii=False, separators=(",", ":")) + "\n")
    return pontos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gera a série histórica de 2022 em JSONL a partir do CSV do TSE."
    )
    parser.add_argument("--csv", default=str(CSV_PADRAO),
                        help=f"CSV de entrada (padrão: {CSV_PADRAO})")
    parser.add_argument("--saida", default=str(SAIDA_PADRAO),
                        help=f"JSONL de saída (padrão: {SAIDA_PADRAO})")
    args = parser.parse_args(argv)

    caminho_csv = Path(args.csv).expanduser()
    saida = Path(args.saida).expanduser()
    try:
        pontos = semeia(caminho_csv, saida)
    except Exception as exc:
        print(f"falha ao semear a série: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    primeiro, ultimo = pontos[0], pontos[-1]
    print(f"{len(pontos)} ponto(s) gravado(s) em {saida}")
    print(f"intervalo: {primeiro['em']} → {ultimo['em']}")
    print(f"apurado: {primeiro['pct_apurado']}% → {ultimo['pct_apurado']}%")
    print(f"votos válidos no último ponto: {ultimo['vv']:,}".replace(",", "."))
    if ultimo["candidatos"]:
        placar = " · ".join(
            f"{c['nome']} {c['pct']:.2f}% ({c['votos']:,})".replace(",", ".")
            for c in ultimo["candidatos"]
        )
        print(f"último ponto: {placar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
