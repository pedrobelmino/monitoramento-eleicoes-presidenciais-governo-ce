#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gera_municipios_secoes.py — conversor dos dumps abertos do TSE para o mapa eleitoral.

POR QUE ESTE SCRIPT EXISTE
--------------------------
O MapLibre do projeto precisa de dois arquivos pequenos e estáveis:

  * dados/municipios.json — votos por município, totais, líder e margem;
  * dados/secoes.json    — um ponto por seção (lat/lon do local de votação) para
                           desenhar o mapa de calor sem carregar o CSV gigante.

As entradas são os ZIPs oficiais do TSE:

  * votacao_secao_2026_BR.zip — um CSV de ~1,05 GB, só Presidente/eleição 6257/turno 1;
  * eleitorado_local_votacao_2026.zip — 29 CSVs (27 UFs + ZZ + BRASIL) com as
    coordenadas; ZZ e BRASIL são ignorados.

Os dois CSVs usam ';' como separador, aspas em todos os campos, latin-1 e CRLF. O CSV
de votos é lido em streaming (nunca inteiro para a memória), porque são ~3,8 milhões de
linhas. As coordenadas vêm em arquivo separado, por seção.

O exterior (SG_UF = "ZZ") é ignorado no modo BR: não é UF, e o TSE não publica JSON
oficial para ele (o arquivo de coordenadas também traz ZZ, que é descartado).

A CONTA DE FECHAMENTO
---------------------
`validos` é a soma dos votos de TODOS os votáveis que não são 95 (branco) nem 96 (nulo),
inclusive o votável sem registro (aquele que aparece no dump mas não no JSON oficial).
Portanto:

    validos + brancos + nulos = total (soma de QT_VOTOS do dump)

No JSON oficial do TSE, `tv - vv` engloba brancos, nulos e o votável sem registro
(`v.vnt`). É por isso que a soma dos votos de candidatos pode ficar alguns votos acima
de `vv`: esses poucos votos não são de candidato registrado e o TSE os contabiliza
fora dos votos válidos. O script confere cada candidato contra o JSON oficial da UF e
sai com código 0 quando fecha (ou quando não há JSON oficial para conferir) e 2 quando
alguma diferença aparece; os arquivos são gravados nos dois casos, para inspeção.

Uso:
    python3 gera_municipios_secoes.py --uf CE [--bruto DIR] [--saida DIR]
                                      [--cache-oficial DIR] [--baixar]
                                      [--limite-linhas N]
"""

import argparse
import csv
import gzip
import io
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.request
import zipfile
import zlib
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Constantes do pleito e dos arquivos
# ---------------------------------------------------------------------------

ELEICAO = "6257"
TURNO = 1
CARGO = "Presidente"
COD_BRANCO = "95"
COD_NULO = "96"

FONTE = (
    "TSE dados abertos — votacao_secao_2026_BR.zip (eleição 6257, turno 1) "
    "+ eleitorado_local_votacao_2026.zip"
)

URL_VOTOS = (
    "https://cdn.tse.jus.br/estatistica/sead/odsele/votacao_secao/"
    "votacao_secao_2026_BR.zip"
)
URL_COORDS = (
    "https://cdn.tse.jus.br/estatistica/sead/odsele/eleitorado_locais_votacao/"
    "eleitorado_local_votacao_2026.zip"
)
URL_OFICIAL = (
    "https://resultados.tse.jus.br/oficial/ele2026/6257/dados/"
    "{uf}/{uf}-c0001-e006257-u.json"
)
URL_IBGE = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"

UFS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
    "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
    "SP", "SE", "TO",
}

# User-Agent de navegador: o CDN e o TSE recusam clientes sem cabeçalho.
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Funções auxiliares de conversão e normalização
# ---------------------------------------------------------------------------

def para_int(texto):
    """Converte texto em int; devolve None quando vazio ou inválido."""
    if texto is None:
        return None
    t = str(texto).strip()
    if not t:
        return None
    try:
        return int(t)
    except ValueError:
        try:
            return int(float(t.replace(",", ".")))
        except ValueError:
            return None


def normaliza_num(texto):
    """Normaliza o número de votável (remove zeros à esquerda quando numérico)."""
    t = "" if texto is None else str(texto).strip()
    if t.isdigit():
        try:
            return str(int(t))
        except ValueError:
            return t
    return t


def chave_ordem(texto):
    """Chave de ordenação determinística para números de candidato."""
    if texto.isdigit():
        return (0, int(texto))
    return (1, texto)


def normaliza_nome(texto):
    """Maiúsculas, sem acento, sem pontuação e com espaços colapsados."""
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.upper()
    t = re.sub(r"[^A-Z0-9]+", " ", t)
    return " ".join(t.split())


def converte_coord(texto):
    """Converte '-7,042928' em float. Devolve None para vazio, -1 ou inválido."""
    if texto is None:
        return None
    t = str(texto).strip().replace(",", ".")
    if not t:
        return None
    try:
        valor = float(t)
    except ValueError:
        return None
    if not math.isfinite(valor) or valor == -1:
        return None
    return valor


def coordenada_valida(lat, lon):
    """Só aceita o par completo e dentro dos limites geográficos."""
    return (
        lat is not None
        and lon is not None
        and -90.0 <= lat <= 90.0
        and -180.0 <= lon <= 180.0
    )


def iso_do_dump(data_txt, hora_txt):
    """
    Timestamp determinístico para 'gerado_em'.

    Usa a data/hora de geração do próprio dump (DT_GERACAO/HH_GERACAO) em vez do
    relógio da máquina. Assim, rodar duas vezes sobre o mesmo dump produz o mesmo
    arquivo byte a byte (o horário de Brasília é fixo em -03:00 no dump do TSE).
    """
    if data_txt and hora_txt:
        try:
            dt = datetime.strptime(
                f"{data_txt.strip()} {hora_txt.strip()}", "%d/%m/%Y %H:%M:%S"
            )
            return dt.replace(tzinfo=timezone(timedelta(hours=-3))).isoformat(
                timespec="seconds"
            )
        except ValueError:
            pass
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# HTTP (JSON oficial, IBGE e download dos ZIPs)
# ---------------------------------------------------------------------------

def abre_url(url, timeout=30):
    """Abre uma URL e devolve os bytes, descomprimindo gzip/deflate se preciso."""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "application/json,text/html,*/*",
            "Accept-Encoding": "gzip, deflate",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        bruto = resp.read()
        codificacao = (resp.headers.get("Content-Encoding") or "").lower()
    if bruto[:2] == b"\x1f\x8b" or codificacao == "gzip":
        bruto = gzip.decompress(bruto)
    elif codificacao == "deflate":
        try:
            bruto = zlib.decompress(bruto)
        except zlib.error:
            bruto = zlib.decompress(bruto, -zlib.MAX_WBITS)
    return bruto


def baixa_zip(url, destino):
    """Baixa um ZIP para 'destino' mostrando progresso a cada 20 MB."""
    temporario = destino + ".part"
    print(f"[download] {url}")
    try:
        os.makedirs(os.path.dirname(destino) or ".", exist_ok=True)
        req = urllib.request.Request(
            url, headers={"User-Agent": UA, "Accept": "*/*"}
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = para_int(resp.headers.get("Content-Length")) or 0
            lido = 0
            proximo_aviso = 20 * 1024 * 1024
            with open(temporario, "wb") as saida:
                while True:
                    bloco = resp.read(1024 * 1024)
                    if not bloco:
                        break
                    saida.write(bloco)
                    lido += len(bloco)
                    if lido >= proximo_aviso:
                        if total:
                            print(
                                f"  {lido / 1048576:.0f} MB de "
                                f"{total / 1048576:.0f} MB"
                            )
                        else:
                            print(f"  {lido / 1048576:.0f} MB")
                        proximo_aviso += 20 * 1024 * 1024
        if total and lido != total:
            raise IOError(
                f"download incompleto: {lido} de {total} bytes"
            )
        os.replace(temporario, destino)
        print(f"[download] concluído: {destino} ({lido / 1048576:.0f} MB)")
    except Exception as erro:  # noqa: BLE001 - queremos mensagem clara ao usuário
        try:
            os.remove(temporario)
        except OSError:
            pass
        print(f"ERRO ao baixar {url}: {erro}", file=sys.stderr)
        raise SystemExit(3)


def garante_entradas(bruto, baixar):
    """Garante os dois ZIPs de entrada; sem --baixar, falha com código 3."""
    entradas = [
        ("votacao_secao_2026_BR.zip", URL_VOTOS),
        ("eleitorado_local_votacao_2026.zip", URL_COORDS),
    ]
    faltando = []
    for nome, url in entradas:
        caminho = os.path.join(bruto, nome)
        if os.path.exists(caminho):
            continue
        if baixar:
            baixa_zip(url, caminho)
        else:
            faltando.append(caminho)
    if faltando:
        print("ERRO: arquivo(s) de entrada ausente(s):", file=sys.stderr)
        for caminho in faltando:
            print(f"  {caminho}", file=sys.stderr)
        print(
            "Rode de novo com --baixar para baixar do CDN do TSE, ou informe "
            "--bruto apontando para o diretório correto.",
            file=sys.stderr,
        )
        raise SystemExit(3)


# ---------------------------------------------------------------------------
# Leitura do CSV de votos
# ---------------------------------------------------------------------------

def escolhe_membro_csv(z, dica=""):
    """Escolhe o membro .csv do ZIP (ignora leiame.pdf e afins)."""
    membros = [
        info.filename
        for info in z.infolist()
        if info.filename.lower().endswith(".csv")
    ]
    if dica:
        for nome in membros:
            if dica.lower() in nome.lower():
                return nome
    if len(membros) == 1:
        return membros[0]
    if not membros:
        raise RuntimeError("o ZIP não tem nenhum membro .csv")
    return sorted(membros)[0]


def le_votos(caminho_zip, uf_alvo, limite_linhas=None):
    """
    Lê o CSV de votos em streaming e devolve as agregações.

    Estruturas:
      municipios[cd_tse] = [uf, nome, {num: votos}, brancos, nulos, total]
      votos_uf[uf]       = {num: votos}          (só candidatos, sem 95/96)
      brancos_uf[uf]     = int
      nulos_uf[uf]       = int
      secoes_uf[uf]      = {(cd_tse, zona, secao): [vb, vn, total, nr_local,
                                                    idx0, qtd0, idx1, qtd1, ...]}
      nums_idx           = [num, ...]            (índice compacto do candidato)
      nomes_dump[num]    = NM_VOTAVEL
    """
    municipios = {}
    votos_uf = {}
    brancos_uf = {}
    nulos_uf = {}
    secoes_uf = {}
    nomes_dump = {}
    indices = {}
    nums_idx = []
    data_ger = None
    hora_ger = None
    lidas = 0
    inicio = time.time()

    with zipfile.ZipFile(caminho_zip) as z:
        nome_membro = escolhe_membro_csv(z, "votacao_secao")
        with z.open(nome_membro) as bruto:
            with io.TextIOWrapper(bruto, encoding="latin-1", newline="") as texto:
                leitor = csv.reader(texto, delimiter=";", quotechar='"')
                primeira = True
                for linha in leitor:
                    if primeira:
                        primeira = False
                        if linha and linha[0].strip().upper() == "DT_GERACAO":
                            continue
                    if limite_linhas is not None and lidas >= limite_linhas:
                        break
                    lidas += 1
                    if lidas % 500000 == 0:
                        print(
                            f"  ... {lidas} linhas em "
                            f"{time.time() - inicio:.1f}s",
                            flush=True,
                        )
                    if len(linha) < 22:
                        continue
                    # O dump já vem filtrado, mas conferir o pleito evita misturar
                    # arquivos de outros anos/turnos por engano.
                    if linha[6].strip() != ELEICAO or linha[5].strip() != str(TURNO):
                        continue
                    uf = linha[10].strip().upper()
                    if not uf:
                        continue
                    # "ZZ" é o exterior: não é UF, não entra no recorte por estado
                    # e o TSE não publica JSON oficial para ele.
                    if uf not in UFS:
                        continue
                    if uf_alvo != "BR" and uf != uf_alvo:
                        continue

                    cd_tse = para_int(linha[13])
                    zona = para_int(linha[15])
                    secao = para_int(linha[16])
                    if cd_tse is None or zona is None or secao is None:
                        continue
                    qt = para_int(linha[21])
                    if qt is None:
                        qt = 0
                    num = normaliza_num(linha[19])
                    if not num:
                        continue

                    if data_ger is None:
                        data_ger = linha[0]
                        hora_ger = linha[1]

                    # --- município ---
                    mun = municipios.get(cd_tse)
                    if mun is None:
                        mun = [uf, linha[14].strip(), {}, 0, 0, 0]
                        municipios[cd_tse] = mun
                    elif not mun[1]:
                        mun[1] = linha[14].strip()
                    mun[5] += qt

                    # --- seção (guarda pares índice/quantidade, bem compacto) ---
                    dic_sec = secoes_uf.get(uf)
                    if dic_sec is None:
                        dic_sec = {}
                        secoes_uf[uf] = dic_sec
                    chave = (cd_tse, zona, secao)
                    sec = dic_sec.get(chave)
                    if sec is None:
                        nr_local_voto = (
                            para_int(linha[22]) if len(linha) > 22 else None
                        )
                        if nr_local_voto is not None and nr_local_voto < 0:
                            nr_local_voto = None
                        sec = [0, 0, 0, nr_local_voto]
                        dic_sec[chave] = sec
                    sec[2] += qt

                    if num == COD_BRANCO:
                        mun[3] += qt
                        brancos_uf[uf] = brancos_uf.get(uf, 0) + qt
                        sec[0] += qt
                    elif num == COD_NULO:
                        mun[4] += qt
                        nulos_uf[uf] = nulos_uf.get(uf, 0) + qt
                        sec[1] += qt
                    else:
                        votos = mun[2]
                        votos[num] = votos.get(num, 0) + qt
                        mapa_uf = votos_uf.get(uf)
                        if mapa_uf is None:
                            mapa_uf = {}
                            votos_uf[uf] = mapa_uf
                        mapa_uf[num] = mapa_uf.get(num, 0) + qt
                        if num not in nomes_dump:
                            nomes_dump[num] = linha[20].strip()
                        idx = indices.get(num)
                        if idx is None:
                            idx = len(nums_idx)
                            indices[num] = idx
                            nums_idx.append(num)
                        sec.append(idx)
                        sec.append(qt)

    print(
        f"  votos: {lidas} linhas em {time.time() - inicio:.1f}s "
        f"({len(municipios)} municípios)"
    )
    return {
        "municipios": municipios,
        "votos_uf": votos_uf,
        "brancos_uf": brancos_uf,
        "nulos_uf": nulos_uf,
        "secoes_uf": secoes_uf,
        "nomes_dump": nomes_dump,
        "indices": indices,
        "nums_idx": nums_idx,
        "data_ger": data_ger,
        "hora_ger": hora_ger,
        "linhas": lidas,
    }


# ---------------------------------------------------------------------------
# Leitura das coordenadas
# ---------------------------------------------------------------------------

def le_coordenadas(caminho_zip, ufs_alvo):
    """
    Lê os CSVs por UF do ZIP de locais de votação.

    Devolve {(cd_tse, zona, secao): [nr_local, lat, lon]}. Quando há mais de uma
    linha para a mesma seção (seção agregada), fica com a primeira coordenada
    válida encontrada, mas aproveita um NR_LOCAL novo se o anterior era vazio.
    """
    mapa = {}
    padrao = re.compile(r"_([A-Za-z]{2})\.csv$", re.IGNORECASE)
    with zipfile.ZipFile(caminho_zip) as z:
        for info in z.infolist():
            nome = info.filename
            if not nome.lower().endswith(".csv"):
                continue
            achado = padrao.search(nome)
            if not achado:
                continue
            uf = achado.group(1).upper()
            if uf not in UFS or uf not in ufs_alvo:
                continue

            print(f"  coordenadas: lendo {uf} ...", flush=True)
            inicio = time.time()
            lidas = 0
            with z.open(nome) as bruto:
                with io.TextIOWrapper(
                    bruto, encoding="latin-1", newline=""
                ) as texto:
                    leitor = csv.reader(texto, delimiter=";", quotechar='"')
                    primeira = True
                    for linha in leitor:
                        if primeira:
                            primeira = False
                            if linha and linha[0].strip().upper() == "DT_GERACAO":
                                continue
                        if len(linha) < 24:
                            continue
                        if linha[6].strip().upper() != uf:
                            continue
                        cd_tse = para_int(linha[7])
                        zona = para_int(linha[9])
                        secao = para_int(linha[10])
                        if cd_tse is None or zona is None or secao is None:
                            continue
                        lidas += 1
                        chave = (cd_tse, zona, secao)
                        nr_local = para_int(linha[14])
                        if nr_local is not None and nr_local < 0:
                            nr_local = None
                        lat = converte_coord(linha[22])
                        lon = converte_coord(linha[23])
                        if not coordenada_valida(lat, lon):
                            lat = None
                            lon = None

                        anterior = mapa.get(chave)
                        if anterior is None:
                            mapa[chave] = [nr_local, lat, lon]
                        else:
                            if anterior[1] is None and lat is not None:
                                anterior[0] = nr_local
                                anterior[1] = lat
                                anterior[2] = lon
                            elif anterior[0] is None and nr_local is not None:
                                anterior[0] = nr_local
            print(
                f"  coordenadas: {uf} com {lidas} linhas em "
                f"{time.time() - inicio:.1f}s"
            )
    return mapa


# ---------------------------------------------------------------------------
# IBGE
# ---------------------------------------------------------------------------

def mapa_ibge(uf, saida):
    """
    Devolve {nome_normalizado: cd_ibge} para a UF.

    Usa o cache dados/ibge_municipios_<UF>.json quando existe; senão baixa da API
    do IBGE e grava o cache. Sem rede e sem cache, devolve vazio (cd_ibge null,
    nome registrado em conferencia.nomes_sem_ibge).
    """
    caminho = os.path.join(saida, f"ibge_municipios_{uf}.json")
    dados = None
    if os.path.exists(caminho):
        try:
            with open(caminho, "r", encoding="utf-8-sig") as arquivo:
                dados = json.load(arquivo)
        except (OSError, ValueError) as erro:
            print(f"[aviso] cache IBGE inválido para {uf}: {erro}", file=sys.stderr)
            dados = None

    if not dados:
        url = URL_IBGE.format(uf=uf)
        try:
            bruto = abre_url(url, timeout=30)
            dados = json.loads(bruto.decode("utf-8"))
            lista = dados.get("municipios") if isinstance(dados, dict) else dados
            if not isinstance(lista, list) or not lista:
                raise ValueError("resposta do IBGE sem lista de municípios")
            with open(caminho, "w", encoding="utf-8", newline="\n") as arquivo:
                json.dump(dados, arquivo, ensure_ascii=False, indent=1)
                arquivo.write("\n")
            print(f"  IBGE: cache gravado em {caminho}")
        except Exception as erro:  # noqa: BLE001 - falha de rede não aborta
            print(
                f"[aviso] não foi possível obter municípios do IBGE para {uf}: "
                f"{erro}",
                file=sys.stderr,
            )
            return {}

    lista = dados.get("municipios") if isinstance(dados, dict) else dados
    if not isinstance(lista, list):
        return {}
    mapa = {}
    for item in lista:
        if not isinstance(item, dict):
            continue
        ident = item.get("id", item.get("cd_ibge"))
        nome = item.get("nome", item.get("nm"))
        if ident is None or not nome:
            continue
        ident = para_int(ident)
        if ident is None:
            continue
        mapa[normaliza_nome(str(nome))] = ident
    return mapa


# ---------------------------------------------------------------------------
# JSON oficial da UF (fonte de verdade da conferência)
# ---------------------------------------------------------------------------

def interpreta_oficial(dados, url):
    """Extrai vv, tv, brancos/nulos/sem-registro e os candidatos do JSON do TSE."""
    try:
        cargo = dados["carg"][0]
    except (KeyError, IndexError, TypeError) as erro:
        raise ValueError(f"JSON oficial sem carg[0]: {erro}") from erro

    v = dados.get("v") or {}
    resultado = {
        "fonte": url,
        "vv": para_int(v.get("vv")),
        "tv": para_int(v.get("tv")),
        "vb": para_int(v.get("vb")) or 0,
        "vn": para_int(v.get("vn")) or 0,
        "vnt": para_int(v.get("vnt")) or 0,
        "candidatos": {},
        "ordem": [],
    }
    if resultado["vv"] is None or resultado["tv"] is None:
        raise ValueError("JSON oficial sem v.vv ou v.tv")

    for agremiacao in cargo.get("agr", []):
        for partido in agremiacao.get("par", []):
            for cand in partido.get("cand", []):
                num = normaliza_num(cand.get("n"))
                if not num or num in resultado["candidatos"]:
                    continue
                vap = para_int(cand.get("vap"))
                if vap is None:
                    vap = 0
                nmu = cand.get("nmu") or cand.get("nm") or ""
                nm = cand.get("nm") or nmu
                resultado["candidatos"][num] = {
                    "vap": vap,
                    "nmu": nmu,
                    "nm": nm,
                    "dvt": cand.get("dvt") or "",
                }
                resultado["ordem"].append(num)
    if not resultado["candidatos"]:
        raise ValueError("JSON oficial sem candidatos em carg[0]")
    return resultado


def le_oficial(uf, cache_dir):
    """
    Lê (ou baixa) o JSON oficial da UF. Devolve (dados, erro).

    O cache tem prioridade; se não existir, baixa com timeout de 30 s e grava.
    Em caso de falha devolve (None, motivo) e a conferência fica 'sem_conferencia'.
    """
    nome = f"{uf.lower()}-c0001-e006257-u.json"
    caminho = os.path.join(cache_dir, nome)
    url = URL_OFICIAL.format(uf=uf.lower())

    dados = None
    if os.path.exists(caminho):
        try:
            with open(caminho, "r", encoding="utf-8-sig") as arquivo:
                dados = json.load(arquivo)
        except (OSError, ValueError) as erro:
            print(
                f"[aviso] cache oficial inválido para {uf}: {erro}",
                file=sys.stderr,
            )
            dados = None

    if dados is None:
        try:
            bruto = abre_url(url, timeout=30)
            dados = json.loads(bruto.decode("utf-8-sig"))
            os.makedirs(cache_dir, exist_ok=True)
            with open(caminho, "w", encoding="utf-8", newline="\n") as arquivo:
                json.dump(dados, arquivo, ensure_ascii=False)
                arquivo.write("\n")
            print(f"  oficial: cache gravado em {caminho}")
        except Exception as erro:  # noqa: BLE001 - rede não pode abortar o script
            return None, f"{type(erro).__name__}: {erro}"

    try:
        return interpreta_oficial(dados, url), None
    except (KeyError, TypeError, ValueError) as erro:
        return None, f"formato inesperado: {erro}"


# ---------------------------------------------------------------------------
# Ordenação e montagem das saídas
# ---------------------------------------------------------------------------

def ordena_por_votos(mapa):
    """Números de candidato ordenados por votos desc e, no empate, pelo número."""
    return [
        num
        for num, _ in sorted(
            mapa.items(), key=lambda item: (-item[1], chave_ordem(item[0]))
        )
    ]


def nomes_oficiais(oficiais):
    """Junta nmu/nm oficiais de todas as UFs (o mesmo candidato no Brasil todo)."""
    mapa = {}
    for uf in sorted(oficiais):
        dados = oficiais[uf]
        if not dados:
            continue
        for num in dados["ordem"]:
            if num not in mapa:
                mapa[num] = dados["candidatos"][num]
    return mapa


def monta_municipios(dados, ufs, top2_uf, cands_globais, mapas_ibge):
    """Monta a lista de municípios e as somas por UF para a conferência."""
    municipios_saida = []
    soma_mun_uf = {uf: {} for uf in ufs}
    nomes_sem_ibge = {uf: [] for uf in ufs}
    rank_global = {num: i for i, num in enumerate(cands_globais)}

    for cd_tse in sorted(dados["municipios"]):
        mun = dados["municipios"][cd_tse]
        uf = mun[0]
        if uf not in ufs:
            continue
        nome = mun[1]
        votos = mun[2]
        brancos = mun[3]
        nulos = mun[4]
        total = mun[5]
        validos = sum(votos.values())
        forcados = top2_uf.get(uf, [])

        mapa_votos = {}
        for num in cands_globais:
            qtd = votos.get(num, 0)
            if qtd > 0 or num in forcados:
                mapa_votos[num] = qtd
        # Garante que nenhum votável do município fique de fora (não deve ocorrer).
        for num, qtd in votos.items():
            if num not in mapa_votos and qtd > 0:
                mapa_votos[num] = qtd

        participantes = list(mapa_votos)
        if participantes:
            participantes.sort(
                key=lambda num: (
                    -votos.get(num, 0),
                    rank_global.get(num, 10 ** 9),
                    chave_ordem(num),
                )
            )
            lider = participantes[0]
            segundo = participantes[1] if len(participantes) > 1 else None
            margem = votos.get(lider, 0) - (
                votos.get(segundo, 0) if segundo is not None else 0
            )
        else:
            lider = None
            margem = 0
        margem_pct = round(margem / validos * 100, 2) if validos else 0.0

        contagem = dados["contagens"][uf]
        cd_ibge = mapas_ibge.get(uf, {}).get(normaliza_nome(nome))
        if cd_ibge is None:
            nomes_sem_ibge[uf].append(nome)

        municipios_saida.append(
            {
                "cd_tse": cd_tse,
                "cd_ibge": cd_ibge,
                "nome": nome,
                "votos": mapa_votos,
                "votos_outros": 0,
                "brancos": brancos,
                "nulos": nulos,
                "validos": validos,
                "total": total,
                "secoes": contagem[0].get(cd_tse, 0),
                "secoes_com_coordenada": contagem[1].get(cd_tse, 0),
                "lider": lider,
                "margem": margem,
                "margem_pct": margem_pct,
            }
        )

        soma_uf = soma_mun_uf[uf]
        for num, qtd in votos.items():
            soma_uf[num] = soma_uf.get(num, 0) + qtd

    for uf in ufs:
        nomes_sem_ibge[uf] = sorted(set(nomes_sem_ibge[uf]))
    return municipios_saida, soma_mun_uf, nomes_sem_ibge


def escreve_secoes(
    saida,
    uf_arg,
    ufs,
    dados,
    top2_uf,
    coords,
    gerado_em,
    nomes_oficiais_mapa,
):
    """
    Grava secoes.json (UF única) ou secoes_<UF>.json (modo BR).

    Devolve {uf: {num: votos}} com a soma das seções, usada na conferência.
    """
    soma_sec_uf = {}
    for uf in ufs:
        dic = dados["secoes_uf"].get(uf, {})
        top2 = top2_uf.get(uf, [])
        idx_top = [dados["indices"].get(num) for num in top2]
        i1 = idx_top[0] if len(idx_top) > 0 else None
        i2 = idx_top[1] if len(idx_top) > 1 else None

        campos = ["zona", "secao", "cd_tse", "nr_local", "lat", "lon"]
        campos.extend(f"v{num}" for num in top2)
        campos.extend(["voutros", "vbranco", "vnulo", "total"])

        candidatos = {}
        for num in top2:
            info = nomes_oficiais_mapa.get(num)
            if info is not None and info.get("nmu"):
                candidatos[num] = info["nmu"]
            else:
                candidatos[num] = dados["nomes_dump"].get(num, num)

        municipios = {
            str(cd): mun[1]
            for cd, mun in sorted(dados["municipios"].items())
            if mun[0] == uf
        }

        lista = []
        soma_sec = {}
        for chave, sec in sorted(dic.items()):
            cd_tse, zona, secao = chave
            vb = sec[0]
            vn = sec[1]
            total = sec[2]
            nr_local = sec[3]

            coord = coords.get(chave)
            lat = None
            lon = None
            if coord is not None:
                if coord[0] is not None:
                    nr_local = coord[0]
                lat, lon = coord[1], coord[2]
            if lat is not None and lon is not None:
                lat = round(lat, 6)
                lon = round(lon, 6)
            else:
                lat = None
                lon = None

            v1 = 0
            v2 = 0
            outros = 0
            i = 4
            while i < len(sec):
                idx = sec[i]
                qtd = sec[i + 1]
                num = dados["nums_idx"][idx]
                soma_sec[num] = soma_sec.get(num, 0) + qtd
                if i1 is not None and idx == i1:
                    v1 += qtd
                elif i2 is not None and idx == i2:
                    v2 += qtd
                else:
                    outros += qtd
                i += 2

            # A quantidade de colunas de voto acompanha o top 2 da UF; assim
            # 'campos' e cada linha da matriz têm sempre o mesmo tamanho.
            valores_v = []
            if len(top2) > 0:
                valores_v.append(v1)
            if len(top2) > 1:
                valores_v.append(v2)
            lista.append(
                [zona, secao, cd_tse, nr_local, lat, lon]
                + valores_v
                + [outros, vb, vn, total]
            )

        # Nome sempre com a UF (secoes_CE.json mesmo em UF única): o modo --uf CE
        # e o modo --uf BR produzem o mesmo esquema de nomes, que é o que o mapa
        # do Ceará e o painel por UF consomem.
        nome_arquivo = f"secoes_{uf}.json"
        caminho = os.path.join(saida, nome_arquivo)
        objeto = {
            "gerado_em": gerado_em,
            "fonte": FONTE,
            "uf": uf,
            "eleicao": ELEICAO,
            "turno": TURNO,
            "campos": campos,
            "candidatos": candidatos,
            "municipios": municipios,
            "secoes": lista,
        }
        with open(caminho, "w", encoding="utf-8", newline="\n") as arquivo:
            json.dump(
                objeto,
                arquivo,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            arquivo.write("\n")
        print(f"  seções: {uf} com {len(lista)} pontos -> {caminho}")
        soma_sec_uf[uf] = soma_sec

        # Libera memória antes da próxima UF.
        dados["secoes_uf"].pop(uf, None)
        for chave in dic:
            coords.pop(chave, None)

    return soma_sec_uf


# ---------------------------------------------------------------------------
# Conferência
# ---------------------------------------------------------------------------

def confere(
    uf,
    dados,
    oficial,
    soma_mun,
    soma_sec,
    contagem,
    nomes_sem_ibge_uf,
):
    """Monta o relatório estruturado de uma UF e diz se há divergência."""
    votos_uf = dados["votos_uf"].get(uf, {})
    validos = sum(votos_uf.values())
    brancos = dados["brancos_uf"].get(uf, 0)
    nulos = dados["nulos_uf"].get(uf, 0)
    total = sum(
        mun[5] for mun in dados["municipios"].values() if mun[0] == uf
    )
    soma = validos + brancos + nulos
    checagem = {
        "validos": validos,
        "brancos": brancos,
        "nulos": nulos,
        "soma": soma,
        "total": total,
        "dif": soma - total,
    }

    com_voto = sum(contagem[0].values())
    com_coord = sum(contagem[1].values())
    cobertura = {
        "com_voto": com_voto,
        "com_coordenada": com_coord,
        "pct": round(com_coord / com_voto * 100, 2) if com_voto else 0.0,
    }

    if oficial is None:
        uf_json = None
        dif_municipios = {}
        dif_secoes = {}
        sem_oficial = []
        total_sem_oficial = 0
        status = "divergente" if checagem["dif"] != 0 else "sem_conferencia"
        explicacao = (
            "JSON oficial da UF indisponível; a conferência de candidatos não foi "
            "feita. validos, brancos e nulos vêm do próprio dump e "
            f"fecham em {total} votos."
        )
    else:
        uf_json = {
            "fonte": oficial["fonte"],
            "vv": oficial["vv"],
            "tv": oficial["tv"],
            "candidatos": {
                num: oficial["candidatos"][num]["vap"]
                for num in oficial["ordem"]
            },
        }
        dif_municipios = {
            num: soma_mun.get(num, 0) - oficial["candidatos"][num]["vap"]
            for num in oficial["ordem"]
        }
        dif_secoes = {
            num: soma_sec.get(num, 0) - soma_mun.get(num, 0)
            for num in ordena_por_votos(soma_mun)
        }

        sem_oficial = []
        for num in ordena_por_votos(soma_mun):
            if num in oficial["candidatos"]:
                continue
            if soma_mun.get(num, 0) <= 0:
                continue
            sem_oficial.append(
                {
                    "n": num,
                    "nome": dados["nomes_dump"].get(num, ""),
                    "votos": soma_mun.get(num, 0),
                }
            )
        total_sem_oficial = sum(item["votos"] for item in sem_oficial)

        divergente = (
            any(valor != 0 for valor in dif_municipios.values())
            or any(valor != 0 for valor in dif_secoes.values())
            or checagem["dif"] != 0
        )
        status = "divergente" if divergente else "ok"

        if total_sem_oficial:
            explicacao = (
                f"{total_sem_oficial} votos de votável sem registro (presente no "
                "dump e ausente do JSON oficial); o TSE os contabiliza em tv - vv, "
                "junto com brancos (95) e nulos (96). Por isso a soma dos votos de "
                "candidatos (validos) pode superar vv."
            )
        else:
            explicacao = (
                "Nenhum votável sem registro no dump. validos é a soma dos "
                "candidatos; brancos (95) e nulos (96) ficam fora; "
                "validos + brancos + nulos = total."
            )
        if oficial.get("vnt"):
            explicacao += (
                f" JSON oficial: vv={oficial['vv']}, tv={oficial['tv']}, "
                f"vb={oficial['vb']}, vn={oficial['vn']}, vnt={oficial['vnt']}."
            )

    relatorio = {
        "status": status,
        "uf_json": uf_json,
        "dif_municipios_vs_uf": dif_municipios,
        "dif_secoes_vs_municipios": dif_secoes,
        "checagem_total": checagem,
        "secoes_cobertura": cobertura,
        "votaveis_sem_oficial": sem_oficial,
        "total_votos_sem_oficial": total_sem_oficial,
        "nomes_sem_ibge": nomes_sem_ibge_uf,
        "explicacao_diferenca": explicacao,
    }
    return relatorio


def imprime_relatorio(rel, uf, soma_mun, soma_sec):
    """Imprime o mesmo conteúdo estruturado de conferência, em texto legível."""
    print(f"\n=== CONFERÊNCIA — {uf} ===")
    oficial = rel["uf_json"]
    if oficial is None:
        print("  JSON oficial indisponível: conferência de candidatos não realizada.")
    else:
        print("  SOMA MUNICÍPIOS x JSON OFICIAL DA UF")
        for num, dif in rel["dif_municipios_vs_uf"].items():
            municipios = soma_mun.get(num, 0)
            valor_oficial = oficial["candidatos"].get(num, 0)
            print(
                f"    {num:>3}  municípios={municipios:>10}  "
                f"oficial={valor_oficial:>10}  dif={dif:+d}"
            )

    print("  SOMA SEÇÕES x TOTAIS DOS MUNICÍPIOS")
    for num, dif in rel["dif_secoes_vs_municipios"].items():
        print(
            f"    {num:>3}  seções={soma_sec.get(num, 0):>10}  "
            f"municípios={soma_mun.get(num, 0):>10}  dif={dif:+d}"
        )

    checagem = rel["checagem_total"]
    print("  VÁLIDOS + BRANCOS + NULOS x TOTAL")
    print(
        f"    {checagem['validos']} + {checagem['brancos']} + "
        f"{checagem['nulos']} = {checagem['soma']}  "
        f"total={checagem['total']}  dif={checagem['dif']:+d}"
    )

    cobertura = rel["secoes_cobertura"]
    print("  SEÇÕES")
    print(
        f"    com voto: {cobertura['com_voto']}  |  "
        f"com coordenada: {cobertura['com_coordenada']}  |  "
        f"cobertura: {cobertura['pct']:.2f}%"
    )

    print("  MUNICÍPIOS SEM CD_IBGE")
    sem_ibge = rel["nomes_sem_ibge"]
    if not sem_ibge:
        print("    nenhum")
    else:
        print("    " + ", ".join(sem_ibge))

    print("  VOTÁVEIS NO DUMP AUSENTES NO JSON OFICIAL")
    sem_oficial = rel["votaveis_sem_oficial"]
    if not sem_oficial:
        print("    nenhum")
    else:
        for item in sem_oficial:
            print(
                f"    {item['n']:>3}  {item['nome']:<35} "
                f"votos={item['votos']}"
            )
        print(
            f"    total sem registro: {rel['total_votos_sem_oficial']} "
            "(o TSE soma em tv - vv, com brancos/nulos)"
        )

    print(f"  STATUS: {rel['status']}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Converte os dumps do TSE em dados/municipios.json e "
            "dados/secoes.json para o mapa MapLibre."
        )
    )
    parser.add_argument(
        "--uf",
        default="CE",
        type=lambda valor: valor.strip().upper(),
        choices=sorted(UFS) + ["BR"],
        help="UF a processar (padrão: CE) ou BR para o Brasil inteiro.",
    )
    parser.add_argument(
        "--bruto",
        default="~/.hermes/eleicao/tse_raw",
        help="diretório dos ZIPs de entrada (padrão: ~/.hermes/eleicao/tse_raw).",
    )
    parser.add_argument(
        "--saida",
        default="dados",
        help="diretório de saída (padrão: dados).",
    )
    parser.add_argument(
        "--cache-oficial",
        default="~/.hermes/eleicao/tse_raw/oficial",
        help=(
            "cache dos JSONs oficiais por UF "
            "(padrão: ~/.hermes/eleicao/tse_raw/oficial)."
        ),
    )
    parser.add_argument(
        "--baixar",
        action="store_true",
        help="baixa do CDN do TSE os ZIPs que estiverem faltando.",
    )
    parser.add_argument(
        "--limite-linhas",
        type=int,
        default=None,
        metavar="N",
        help="processa só as N primeiras linhas do CSV de votos (teste rápido).",
    )
    args = parser.parse_args(argv)

    if args.limite_linhas is not None and args.limite_linhas < 0:
        parser.error("--limite-linhas não pode ser negativo")

    uf_arg = args.uf
    bruto = os.path.expanduser(args.bruto)
    saida = os.path.expanduser(args.saida)
    cache_oficial = os.path.expanduser(args.cache_oficial)
    try:
        os.makedirs(saida, exist_ok=True)
    except OSError as erro:
        print(f"ERRO ao criar o diretório de saída {saida}: {erro}", file=sys.stderr)
        return 3
    garante_entradas(bruto, args.baixar)

    caminho_votos = os.path.join(bruto, "votacao_secao_2026_BR.zip")
    caminho_coords = os.path.join(bruto, "eleitorado_local_votacao_2026.zip")

    try:
        print("[1/5] lendo votos (streaming)")
        dados = le_votos(caminho_votos, uf_arg, args.limite_linhas)

        ufs_dados = sorted({mun[0] for mun in dados["municipios"].values()})
        # Com --limite-linhas é possível não cair nenhuma linha da UF pedida.
        # Mesmo assim as duas saídas devem existir, então mantemos a UF no recorte.
        if not ufs_dados and uf_arg != "BR":
            ufs_dados = [uf_arg]
        ufs_conf = ufs_dados if ufs_dados else (
            [uf_arg] if uf_arg != "BR" else []
        )

        print("[2/5] lendo coordenadas")
        coords = le_coordenadas(caminho_coords, set(ufs_dados))

        print("[3/5] carregando municípios do IBGE")
        mapas_ibge = {uf: mapa_ibge(uf, saida) for uf in ufs_dados}

        print("[4/5] conferindo com os JSONs oficiais")
        oficiais = {}
        erros_oficiais = {}
        for uf in ufs_conf:
            oficial, erro = le_oficial(uf, cache_oficial)
            oficiais[uf] = oficial
            if erro is not None:
                erros_oficiais[uf] = erro
                print(f"[aviso] oficial {uf}: {erro}", file=sys.stderr)

        nomes_of = nomes_oficiais(oficiais)

        totais_cand = {}
        for uf in ufs_dados:
            for num, qtd in dados["votos_uf"].get(uf, {}).items():
                totais_cand[num] = totais_cand.get(num, 0) + qtd
        cands_globais = ordena_por_votos(totais_cand)

        top2_uf = {}
        for uf in ufs_dados:
            top2_uf[uf] = ordena_por_votos(dados["votos_uf"].get(uf, {}))[:2]

        # Conta seções e coberturas por município antes de montar a saída.
        contagens = {}
        for uf in ufs_dados:
            cont = {}
            com_coord = {}
            for chave in dados["secoes_uf"].get(uf, {}):
                cd_tse = chave[0]
                cont[cd_tse] = cont.get(cd_tse, 0) + 1
                coord = coords.get(chave)
                if coord is not None and coordenada_valida(coord[1], coord[2]):
                    com_coord[cd_tse] = com_coord.get(cd_tse, 0) + 1
            contagens[uf] = (cont, com_coord)
        dados["contagens"] = contagens

        print("[5/5] montando e gravando as saídas")
        municipios_saida, soma_mun_uf, nomes_sem_ibge = monta_municipios(
            dados, ufs_dados, top2_uf, cands_globais, mapas_ibge
        )

        gerado_em = iso_do_dump(dados["data_ger"], dados["hora_ger"])
        soma_sec_uf = escreve_secoes(
            saida,
            uf_arg,
            ufs_dados,
            dados,
            top2_uf,
            coords,
            gerado_em,
            nomes_of,
        )

        relatorios = {}
        for uf in ufs_conf:
            relatorios[uf] = confere(
                uf,
                dados,
                oficiais.get(uf),
                soma_mun_uf.get(uf, {}),
                soma_sec_uf.get(uf, {}),
                contagens.get(uf, ({}, {})),
                nomes_sem_ibge.get(uf, []),
            )

        # ----- status geral -----
        if uf_arg == "BR":
            status_lista = [rel["status"] for rel in relatorios.values()]
            if any(status == "divergente" for status in status_lista):
                status_geral = "divergente"
            elif status_lista and all(
                status == "ok" for status in status_lista
            ):
                status_geral = "ok"
            else:
                status_geral = "sem_conferencia"
            sem_conf = sorted(
                uf
                for uf, rel in relatorios.items()
                if rel["status"] == "sem_conferencia"
            )
            conferencia = {
                "status": status_geral,
                "ufs": relatorios,
                "ufs_sem_conferencia": sem_conf,
                "nomes_sem_ibge": sorted(
                    {nome for lista in nomes_sem_ibge.values() for nome in lista}
                ),
                "explicacao_diferenca": (
                    "Conferência por UF contra o JSON oficial de cada estado. "
                    "validos inclui o votável sem registro, que o TSE contabiliza "
                    "em tv - vv junto com brancos e nulos."
                ),
            }
            if erros_oficiais:
                conferencia["erros_oficiais"] = erros_oficiais
        else:
            uf_unica = ufs_conf[0] if ufs_conf else uf_arg
            conferencia = relatorios.get(
                uf_unica,
                {
                    "status": "sem_conferencia",
                    "uf_json": None,
                    "dif_municipios_vs_uf": {},
                    "dif_secoes_vs_municipios": {},
                    "checagem_total": {
                        "validos": 0,
                        "brancos": 0,
                        "nulos": 0,
                        "soma": 0,
                        "total": 0,
                        "dif": 0,
                    },
                    "secoes_cobertura": {
                        "com_voto": 0,
                        "com_coordenada": 0,
                        "pct": 0.0,
                    },
                    "votaveis_sem_oficial": [],
                    "total_votos_sem_oficial": 0,
                    "nomes_sem_ibge": [],
                    "explicacao_diferenca": "Nenhum voto encontrado.",
                },
            )
            if erros_oficiais:
                conferencia["erros_oficiais"] = erros_oficiais

        # ----- totais -----
        validos_total = sum(sum(mapa.values()) for mapa in dados["votos_uf"].values())
        brancos_total = sum(dados["brancos_uf"].values())
        nulos_total = sum(dados["nulos_uf"].values())
        total_total = sum(mun[5] for mun in dados["municipios"].values())
        secoes_total = sum(
            sum(cont[0].values()) for cont in contagens.values()
        )
        totais = {num: totais_cand.get(num, 0) for num in cands_globais}
        totais.update(
            {
                "brancos": brancos_total,
                "nulos": nulos_total,
                "validos": validos_total,
                "total": total_total,
                "municipios": len(municipios_saida),
                "secoes": secoes_total,
            }
        )

        # ----- candidatos -----
        candidatos = []
        for num in cands_globais:
            info = nomes_of.get(num)
            if info is not None:
                nmu = info.get("nmu") or dados["nomes_dump"].get(num, num)
                nm = info.get("nm") or nmu
            else:
                nmu = dados["nomes_dump"].get(num, num)
                nm = nmu
            votos_num = totais_cand.get(num, 0)
            candidatos.append(
                {
                    "n": num,
                    "nmu": nmu,
                    "nm": nm,
                    "votos": votos_num,
                    "pct": (
                        round(votos_num / validos_total * 100, 2)
                        if validos_total
                        else 0.0
                    ),
                }
            )

        objeto = {
            "gerado_em": gerado_em,
            "fonte": FONTE,
            "gerado_por": "gera_municipios_secoes.py",
            "uf": uf_arg,
            "eleicao": ELEICAO,
            "turno": TURNO,
            "cargo": CARGO,
            "candidatos": candidatos,
            "municipios": municipios_saida,
            "totais": totais,
            "conferencia": conferencia,
        }
        caminho_municipios = os.path.join(saida, "municipios.json")
        with open(caminho_municipios, "w", encoding="utf-8", newline="\n") as arquivo:
            json.dump(objeto, arquivo, ensure_ascii=False, indent=1)
            arquivo.write("\n")
        print(f"  municípios: {len(municipios_saida)} -> {caminho_municipios}")

        # ----- relatório final no stdout -----
        for uf in ufs_conf:
            imprime_relatorio(
                relatorios[uf],
                uf,
                soma_mun_uf.get(uf, {}),
                soma_sec_uf.get(uf, {}),
            )

        status_final = conferencia["status"]
        if uf_arg == "BR":
            print("\n=== RESUMO BRASIL ===")
            print(
                f"  municípios: {totais['municipios']}  |  "
                f"seções: {totais['secoes']}  |  "
                f"válidos: {totais['validos']}  |  total: {totais['total']}"
            )
        print(f"\nSTATUS GERAL: {status_final}")
        return 0 if status_final in ("ok", "sem_conferencia") else 2

    except (zipfile.BadZipFile, RuntimeError, OSError, csv.Error) as erro:
        print(f"ERRO ao processar os dumps: {erro}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
