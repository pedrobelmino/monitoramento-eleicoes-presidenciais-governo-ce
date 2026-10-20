"""Coletor do feed público do TSE para a eleição presidencial.

Uma linha de série por mudança de ``(eleicao, fonte_gerada_em)``. O arquivo é
JSONL append-only. Falha de uma UF não interrompe a coleta: a UF entra em
``erros`` e nenhum campo é preenchido com zero por falta de dado.
"""

from __future__ import annotations

import argparse
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from fontes_ufs import NOMES, REGIAO_DE, fontes, json_tse

TEMPO_LIMITE = 25
MAX_WORKERS = 5
TENTATIVAS = 2
ESPERA = 1.5

CABECALHOS = {
    "User-Agent": "Mozilla/5.0 (compatível; painel-presidente)",
    "Accept": "application/json, text/plain, */*",
}

CAMINHO_SERIE = (
    Path.home() / ".hermes" / "eleicao_presidente" / "serie_presidente.jsonl"
)

TURNOS = {"6257": "1º turno", "6258": "2º turno"}

# Motivo da última falha de pega_uf na thread atual. Permite que pega_uf
# continue com a assinatura pública dict | None e que coleta saiba o motivo.
_estado = threading.local()


# ---------------------------------------------------------------------------
# Conversões tolerantes: campo ausente vira None, nunca 0.
# ---------------------------------------------------------------------------


def _inteiro(valor) -> int | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    if texto == "" or texto in {"-", "--"}:
        return None
    texto = texto.replace(".", "").replace(",", "")
    try:
        return int(texto)
    except ValueError:
        try:
            return int(float(texto))
        except ValueError:
            return None


def _percentual(valor) -> float | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    if texto == "" or texto in {"-", "--"}:
        return None
    # O TSE usa vírgula decimal; se houver ponto de milhar, ele sai antes.
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return round(float(texto), 2)
    except ValueError:
        return None


def _texto(valor) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


# ---------------------------------------------------------------------------
# Leitura HTTP
# ---------------------------------------------------------------------------


def _motivo(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, urllib.error.URLError):
        motivo = exc.reason
        if isinstance(motivo, (TimeoutError, socket.timeout)):
            return "timeout"
        if "timed out" in str(motivo).lower():
            return "timeout"
        return f"rede: {motivo}"
    if isinstance(exc, json.JSONDecodeError):
        return "JSON inválido"
    return f"{type(exc).__name__}: {exc}"


def _baixa_json(url: str) -> dict:
    pedido = urllib.request.Request(url, headers=CABECALHOS)
    with urllib.request.urlopen(pedido, timeout=TEMPO_LIMITE) as resposta:
        corpo = resposta.read()
    dados = json.loads(corpo.decode("utf-8"))
    if not isinstance(dados, dict):
        raise ValueError("JSON não é um objeto")
    return dados


def pega_uf(eleicao: str, uf: str) -> dict | None:
    """Baixa o JSON de uma UF (ou ``br``) com até duas tentativas.

    404 não é repetido. Em qualquer falha devolve ``None`` e registra o motivo
    na thread atual para que ``coleta`` monte a linha de ``erros``.
    """
    url = json_tse(uf, eleicao)
    ultimo_motivo: str | None = None

    for tentativa in range(1, TENTATIVAS + 1):
        try:
            dados = _baixa_json(url)
            _estado.erro = None
            return dados
        except urllib.error.HTTPError as exc:
            ultimo_motivo = _motivo(exc)
            if exc.code == 404:
                _estado.erro = ultimo_motivo
                return None
        except Exception as exc:  # rede, timeout, JSON, etc.
            ultimo_motivo = _motivo(exc)

        if tentativa < TENTATIVAS:
            time.sleep(ESPERA)

    _estado.erro = ultimo_motivo or "falha"
    return None


def _tarefa(eleicao: str, uf: str) -> tuple[dict | None, str | None]:
    """Executa pega_uf e devolve (dados, motivo do erro)."""
    _estado.erro = None
    try:
        dados = pega_uf(eleicao, uf)
    except Exception as exc:
        return None, _motivo(exc)
    if dados is None:
        return None, getattr(_estado, "erro", None) or "falha"
    return dados, None


# ---------------------------------------------------------------------------
# Regiões e eleição ativa
# ---------------------------------------------------------------------------


def regioes_de(uf: str) -> str:
    """Nome da região de uma UF (aceita maiúsculas ou minúsculas)."""
    try:
        return REGIAO_DE[str(uf).strip().upper()]
    except KeyError:
        raise ValueError(f"UF desconhecida: {uf!r}") from None


def eleicao_ativa() -> str:
    """6258 se o 2º turno já começou/encerrou; senão 6257.

    Consulta o arquivo nacional de 6258 uma única vez. Qualquer erro de rede
    mantém a coleta no 1º turno.
    """
    try:
        dados = _baixa_json(json_tse("br", "6258"))
        secao = dados.get("s") or {}
        st = _inteiro(secao.get("st"))
        andamento = (_texto(dados.get("and")) or "").lower()
        if (st is not None and st > 0) or andamento == "f":
            return "6258"
    except Exception:
        pass
    return "6257"


# ---------------------------------------------------------------------------
# Normalização do arquivo bruto
# ---------------------------------------------------------------------------


def _data_fonte(dados: dict) -> tuple[str | None, str | None]:
    """Devolve (ISO sem fuso, texto brasileiro) a partir de ``dg``/``hg``."""
    dia = _texto(dados.get("dg"))
    hora = _texto(dados.get("hg"))
    if not dia or not hora:
        return None, None
    texto = f"{dia} {hora}"
    try:
        momento = datetime.strptime(texto, "%d/%m/%Y %H:%M:%S")
    except ValueError:
        return None, texto
    return momento.strftime("%Y-%m-%dT%H:%M:%S"), texto


def _candidatos(dados: dict) -> list[dict]:
    cargos = dados.get("carg") or []
    cargo = next(
        (c for c in cargos if str(c.get("cd")) == "1"),
        cargos[0] if cargos else {},
    )
    lista: list[dict] = []
    for agregado in cargo.get("agr") or []:
        for grupo in agregado.get("par") or []:
            partido = _texto(grupo.get("sg"))
            for candidato in grupo.get("cand") or []:
                lista.append(
                    {
                        "sq": _texto(candidato.get("sqcand")),
                        "nome": _texto(candidato.get("nmu")),
                        "partido": partido,
                        "votos": _inteiro(candidato.get("vap")),
                        "pct": _percentual(candidato.get("pvap")),
                    }
                )

    # Ordena por votos desc; candidato sem votos publicados vai para o fim.
    lista.sort(
        key=lambda c: (
            c["votos"] is not None,
            c["votos"] if c["votos"] is not None else 0,
        ),
        reverse=True,
    )
    return lista


def _bloco(dados: dict) -> dict:
    secao = dados.get("s") or {}
    eleitorado = dados.get("e") or {}
    votos = dados.get("v") or {}
    return {
        "st": _inteiro(secao.get("st")),
        "ts": _inteiro(secao.get("ts")),
        "pct_apurado": _percentual(secao.get("pst")),
        "vv": _inteiro(votos.get("vv")),
        "tv": _inteiro(votos.get("tv")),
        "vb": _inteiro(votos.get("vb")),
        "tvn": _inteiro(votos.get("tvn")),
        "te": _inteiro(eleitorado.get("te")),
        "comparecimento": _inteiro(eleitorado.get("c")),
        "candidatos": _candidatos(dados),
    }


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def coleta(eleicao: str = "auto") -> dict:
    """Coleta as 27 UFs e o nacional e devolve o snapshot completo."""
    if eleicao in (None, "auto"):
        eleicao = eleicao_ativa()
    eleicao = str(eleicao).strip()
    if not eleicao.isdigit():
        raise ValueError(f"eleição inválida: {eleicao!r}")

    turno = TURNOS.get(eleicao, f"turno {eleicao}")
    alvos: list[tuple[str, str]] = [("BR", "br")]
    alvos.extend((linha["uf"], linha["uf"].lower()) for linha in fontes(eleicao))

    resultados: dict[str, dict | None] = {}
    erros: list[str] = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futuros = {
            executor.submit(_tarefa, eleicao, uf): rotulo
            for rotulo, uf in alvos
        }
        for futuro in as_completed(futuros):
            rotulo = futuros[futuro]
            try:
                dados, motivo = futuro.result()
            except Exception as exc:
                dados, motivo = None, _motivo(exc)
            resultados[rotulo] = dados
            if dados is None:
                erros.append(f"{rotulo}: {motivo or 'falha'}")

    dados_br = resultados.get("BR")
    fonte_iso, fonte_texto = (None, None)
    andamento = None
    nacional = None

    if dados_br is not None:
        fonte_iso, fonte_texto = _data_fonte(dados_br)
        andamento = _texto(dados_br.get("and"))
        bruto = _bloco(dados_br)
        nacional = {
            "st": bruto["st"],
            "ts": bruto["ts"],
            "pct_apurado": bruto["pct_apurado"],
            "vv": bruto["vv"],
            "tv": bruto["tv"],
            "vb": bruto["vb"],
            "tvn": bruto["tvn"],
            "te": bruto["te"],
            "comparecimento": bruto["comparecimento"],
            "candidatos": bruto["candidatos"],
        }

    ufs: dict[str, dict] = {}
    for linha in fontes(eleicao):
        uf = linha["uf"]
        dados = resultados.get(uf)
        if dados is None:
            continue
        bruto = _bloco(dados)
        ufs[uf] = {
            "regiao": regioes_de(uf),
            "st": bruto["st"],
            "ts": bruto["ts"],
            "pct_apurado": bruto["pct_apurado"],
            "vv": bruto["vv"],
            "te": bruto["te"],
            "comparecimento": bruto["comparecimento"],
            "candidatos": bruto["candidatos"],
        }

    st_nacional = nacional["st"] if nacional else None
    somas = [bloco["st"] for bloco in ufs.values() if bloco["st"] is not None]
    st_soma_ufs = sum(somas) if somas else None
    diferenca = (
        st_nacional - st_soma_ufs
        if st_nacional is not None and st_soma_ufs is not None
        else None
    )

    erros.sort()

    return {
        "coletado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
        "eleicao": eleicao,
        "turno": turno,
        "fonte_gerada_em": fonte_iso,
        "fonte_gerada_texto": fonte_texto,
        "andamento": andamento,
        "nacional": nacional,
        "ufs": ufs,
        "conferencia": {
            "st_nacional": st_nacional,
            "st_soma_ufs": st_soma_ufs,
            "diferenca": diferenca,
        },
        "erros": erros,
    }


# ---------------------------------------------------------------------------
# Série JSONL
# ---------------------------------------------------------------------------


def _ultimo_registro(caminho: Path) -> dict | None:
    ultimo = None
    try:
        with caminho.open("r", encoding="utf-8") as arquivo:
            for linha in arquivo:
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    registro = json.loads(linha)
                except json.JSONDecodeError:
                    continue
                if isinstance(registro, dict):
                    ultimo = registro
    except OSError:
        return None
    return ultimo


def grava_serie(snap: dict, caminho: Path, forca: bool = False) -> bool:
    """Anexa um snapshot se a dupla (eleição, fonte gerada) mudou."""
    caminho = Path(caminho).expanduser()

    if not forca:
        ultimo = _ultimo_registro(caminho)
        if (
            ultimo is not None
            and str(ultimo.get("eleicao")) == str(snap.get("eleicao"))
            and ultimo.get("fonte_gerada_em") == snap.get("fonte_gerada_em")
        ):
            return False

    caminho.parent.mkdir(parents=True, exist_ok=True)
    linha = json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
    with caminho.open("a", encoding="utf-8") as arquivo:
        arquivo.write(linha + "\n")
    return True


def serie(
    caminho: Path, eleicao: str | None = None, limite: int = 400
) -> list[dict]:
    """Últimas ``limite`` linhas do JSONL, em ordem cronológica."""
    caminho = Path(caminho).expanduser()
    if not caminho.exists():
        return []

    try:
        limite = int(limite)
    except (TypeError, ValueError):
        limite = 400
    if limite <= 0:
        return []

    registros: deque[dict] = deque(maxlen=limite)
    try:
        with caminho.open("r", encoding="utf-8") as arquivo:
            for linha in arquivo:
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    registro = json.loads(linha)
                except json.JSONDecodeError:
                    continue
                if not isinstance(registro, dict):
                    continue
                if eleicao is not None and str(registro.get("eleicao")) != str(eleicao):
                    continue
                registros.append(registro)
    except OSError:
        return []
    return list(registros)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _milhar(valor: int | None) -> str:
    if valor is None:
        return "sem dado"
    return f"{valor:,}".replace(",", ".")


def _pct(valor: float | None) -> str:
    return f"{valor:.2f}%" if valor is not None else "sem dado"


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Coleta o feed público do TSE para presidente."
    )
    parser.add_argument(
        "--eleicao",
        choices=("6257", "6258", "auto"),
        default="auto",
        help="6257 (1º turno), 6258 (2º turno) ou auto (padrão)",
    )
    parser.add_argument(
        "--forca", action="store_true", help="grava mesmo sem mudança na fonte"
    )
    parser.add_argument(
        "--serie",
        default=str(CAMINHO_SERIE),
        help=f"caminho do JSONL (padrão: {CAMINHO_SERIE})",
    )
    parser.add_argument(
        "--verboso", action="store_true", help="mostra detalhes da conferência"
    )
    args = parser.parse_args(argv)

    eleicao = args.eleicao
    if eleicao == "auto":
        eleicao = eleicao_ativa()

    snap = coleta(eleicao)
    nacional = snap["nacional"]
    ufs = snap["ufs"]
    erros = snap["erros"]

    fonte = snap["fonte_gerada_texto"] or "sem dado"
    andamento = snap["andamento"] or "sem dado"
    print(
        f"eleicao {snap['eleicao']} ({snap['turno']}) · "
        f"fonte TSE gerada em {fonte} · andamento {andamento}"
    )

    arquivos_ok = len(ufs) + (1 if nacional is not None else 0)
    print(
        f"{len(ufs)}/27 UFs + nacional · {arquivos_ok} arquivos OK · "
        f"{len(erros)} erro(s)"
    )
    if erros:
        print("erros: " + " · ".join(erros))

    if nacional is None:
        print("nacional: sem dado (falhou)")
    else:
        linha = f"nacional: {_pct(nacional['pct_apurado'])}"
        candidatos = nacional["candidatos"][:2]
        if candidatos:
            partes = [
                f"{c['nome'] or 'sem nome'} {_pct(c['pct'])} "
                f"({_milhar(c['votos'])})"
                for c in candidatos
            ]
            linha += " · " + " × ".join(partes)
        else:
            linha += " · sem candidatos"
        print(linha)

    if args.verboso:
        conferencia = snap["conferencia"]
        print(
            "conferencia: st nacional="
            f"{_milhar(conferencia['st_nacional'])} · "
            f"soma UFs={_milhar(conferencia['st_soma_ufs'])} · "
            f"diferença={_milhar(conferencia['diferenca'])}"
        )

    caminho = Path(args.serie).expanduser()
    estado = "não gravado"
    if nacional is not None:
        try:
            gravou = grava_serie(snap, caminho, forca=args.forca)
            estado = "gravado" if gravou else "sem novidade"
        except OSError as exc:
            estado = f"erro ao gravar: {exc}"

    total = len(serie(caminho))
    print(f"série: {caminho} · {estado} · {total} linha(s)")

    return 0 if nacional is not None else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
