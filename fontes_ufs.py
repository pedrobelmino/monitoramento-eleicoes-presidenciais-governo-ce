"""Fontes oficiais por UF para o painel Presidente.

Tabela de nomes, sites dos TREs e montagem da URL do JSON público do TSE.
Nenhuma URL é adivinhada: o padrão do arquivo é o mesmo das amostras reais
(``br-c0001-e006257-u.json``) e os 27 domínios ``tre-XX.jus.br`` foram
verificados em 20/10/2026.
"""

from __future__ import annotations

import argparse

# Ordem fixa das regiões, exatamente como no CONTRATO.md.
REGIOES: dict[str, list[str]] = {
    "Norte": ["ac", "ap", "am", "pa", "ro", "rr", "to"],
    "Nordeste": ["al", "ba", "ce", "ma", "pb", "pe", "pi", "rn", "se"],
    "Centro-Oeste": ["df", "go", "mt", "ms"],
    "Sudeste": ["es", "mg", "rj", "sp"],
    "Sul": ["pr", "sc", "rs"],
}

# Nome oficial de cada UF (chave em maiúsculas).
NOMES: dict[str, str] = {
    "AC": "Acre",
    "AL": "Alagoas",
    "AM": "Amazonas",
    "AP": "Amapá",
    "BA": "Bahia",
    "CE": "Ceará",
    "DF": "Distrito Federal",
    "ES": "Espírito Santo",
    "GO": "Goiás",
    "MA": "Maranhão",
    "MG": "Minas Gerais",
    "MS": "Mato Grosso do Sul",
    "MT": "Mato Grosso",
    "PA": "Pará",
    "PB": "Paraíba",
    "PE": "Pernambuco",
    "PI": "Piauí",
    "PR": "Paraná",
    "RJ": "Rio de Janeiro",
    "RN": "Rio Grande do Norte",
    "RO": "Rondônia",
    "RR": "Roraima",
    "RS": "Rio Grande do Sul",
    "SC": "Santa Catarina",
    "SE": "Sergipe",
    "SP": "São Paulo",
    "TO": "Tocantins",
}

# Sites oficiais dos Tribunais Regionais Eleitorais (verificados 200).
TRE: dict[str, str] = {
    uf: f"https://www.tre-{uf.lower()}.jus.br/" for uf in NOMES
}

PORTAL_TSE = "https://resultados.tse.jus.br/"

# UF -> região, derivado da ordem fixa acima.
REGIAO_DE: dict[str, str] = {
    uf.upper(): regiao for regiao, ufs in REGIOES.items() for uf in ufs
}

# Ordem canônica de coleta: regiões na ordem do contrato e, dentro delas,
# a ordem em que as UFs aparecem.
ORDEM_UFS: list[str] = [uf.upper() for ufs in REGIOES.values() for uf in ufs]

BASE_JSON = "https://resultados.tse.jus.br/oficial/ele2026"


def json_tse(uf: str, eleicao: str) -> str:
    """Monta a URL do JSON público de uma UF (ou ``br``) para a eleição.

    O diretório usa o número sem zeros à esquerda (``/6257/``) e o nome do
    arquivo usa seis dígitos (``e006257``), como nos arquivos reais do TSE.
    """
    sigla = str(uf).strip().lower()
    if sigla != "br" and sigla.upper() not in NOMES:
        raise ValueError(f"UF desconhecida: {uf!r}")

    numero_txt = str(eleicao).strip()
    if not numero_txt.isdigit():
        raise ValueError(f"eleição inválida: {eleicao!r}")
    numero = int(numero_txt)

    return (
        f"{BASE_JSON}/{numero}/dados/{sigla}/"
        f"{sigla}-c0001-e{numero:06d}-u.json"
    )


def fontes(eleicao: str) -> list[dict]:
    """Devolve as 27 UFs com nome, região, site do TRE e URL do JSON."""
    linhas: list[dict] = []
    for uf in ORDEM_UFS:
        linhas.append(
            {
                "uf": uf,
                "nome": NOMES[uf],
                "regiao": REGIAO_DE[uf],
                "tre": TRE[uf],
                "json": json_tse(uf, eleicao),
            }
        )
    return linhas


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Lista as 27 UFs e as fontes públicas do TSE."
    )
    parser.add_argument(
        "--eleicao",
        default="6257",
        help="número da eleição no TSE (padrão: 6257)",
    )
    args = parser.parse_args()

    for linha in fontes(args.eleicao):
        print(
            f"{linha['uf']} · {linha['nome']} · {linha['regiao']} · "
            f"{linha['tre']} · {linha['json']}"
        )
    print(f"total: {len(fontes(args.eleicao))} UFs")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
