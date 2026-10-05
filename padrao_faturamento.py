"""
Padrao de arquivos do faturamento Hapvida - REGRA a partir de NOV26 (Andre,
05/10/2026). Fonte de verdade da regra (texto pra humanos e pro Cowork):
operadoras/hapvida/PROCEDIMENTO_MENSAL.md, secao "Padrao de arquivos".

So' estes arquivos sao baixados e enviados, nome {TIPO}_{PLANO}_{CODIGO}_{MESANO}.{ext}:
  Saude          BOLETO_SAUDE / NFE_SAUDE (pdf), RELATORIO_ANALITICO_SAUDE (pdf+xlsx),
                 LISTA_ATIVOS_SAUDE (xlsx+pdf)
  Coparticipacao BOLETO_COPARTICIPACAO / NFE_COPARTICIPACAO (pdf),
  (se houver)    RELATORIO_ANALITICO_COPARTICIPACAO (pdf+xlsx)
  Odonto         BOLETO_ODONTO / NFE_ODONTO (pdf), RELATORIO_ANALITICO_ODONTO (pdf+xlsx),
                 LISTA_ATIVOS_ODONTO (xlsx+pdf)

- Relatorio so' do mes: o total do relatorio analitico tem que bater com o
  boleto da competencia (caso real OUT26: copart montado do nosso banco somou
  todos os ciclos abertos - HC2 recebeu R$ 11,6 mil pra um boleto de R$ 5,7 mil).
- CSV nunca vai pro cliente: fica na pasta so' como fonte do XLSX/PDF e da
  conferencia de valor.
"""
import csv
import io
import os
import re

PLANO_DA_MODALIDADE = {"SAUDE": "SAUDE", "ODONTO_PREMIUM": "ODONTO"}
ANEXAVEIS = (".pdf", ".xlsx")
TOLERANCIA = 0.01

_RE_PADRAO = re.compile(
    r"^(?:(?:BOLETO|NFE)_(?:SAUDE|COPARTICIPACAO|ODONTO)_[A-Z0-9]+_[A-Z]{3}\d{2}\.PDF"
    r"|(?:RELATORIO_ANALITICO_(?:SAUDE|COPARTICIPACAO|ODONTO)|LISTA_ATIVOS_(?:SAUDE|ODONTO))"
    r"_[A-Z0-9]+_[A-Z]{3}\d{2}\.(?:PDF|XLSX|CSV))$"
)


def nome(tipo: str, plano: str, codigo: str, comp: str, ext: str) -> str:
    return f"{tipo}_{plano}_{codigo}_{comp}.{ext}"


def planos_do_contrato(plano: str, com_copart: bool) -> list[str]:
    return [plano] + (["COPARTICIPACAO"] if com_copart and plano == "SAUDE" else [])


def esperados(codigo: str, comp: str, plano: str, com_copart: bool) -> list[str]:
    """Os arquivos que vao pro cliente, na ordem do e-mail."""
    out = []
    for p in planos_do_contrato(plano, com_copart):
        out += [nome("BOLETO", p, codigo, comp, "pdf"), nome("NFE", p, codigo, comp, "pdf"),
                nome("RELATORIO_ANALITICO", p, codigo, comp, "pdf"), nome("RELATORIO_ANALITICO", p, codigo, comp, "xlsx")]
    out += [nome("LISTA_ATIVOS", plano, codigo, comp, "xlsx"), nome("LISTA_ATIVOS", plano, codigo, comp, "pdf")]
    return out


def fontes_csv(codigo: str, comp: str, plano: str, com_copart: bool) -> list[str]:
    """CSV oficial que precisa estar na pasta: fonte do XLSX/PDF e da conferencia de valor."""
    out = [nome("RELATORIO_ANALITICO", p, codigo, comp, "csv") for p in planos_do_contrato(plano, com_copart)]
    return out + [nome("LISTA_ATIVOS", plano, codigo, comp, "csv")]


def _presentes(nomes: list[str]) -> set[str]:
    return {n.upper() for n in nomes}


def faltando(nomes: list[str], codigo: str, comp: str, plano: str, com_copart: bool) -> list[str]:
    tem = _presentes(nomes)
    return [e for e in esperados(codigo, comp, plano, com_copart) if e.upper() not in tem]


def faltando_fontes(nomes: list[str], codigo: str, comp: str, plano: str, com_copart: bool) -> list[str]:
    tem = _presentes(nomes)
    return [e for e in fontes_csv(codigo, comp, plano, com_copart) if e.upper() not in tem]


def anexaveis(nomes: list[str], codigo: str, comp: str, plano: str, com_copart: bool) -> list[str]:
    """Nomes reais (como estao na pasta) dos arquivos que podem ir pro cliente."""
    validos = {e.upper() for e in esperados(codigo, comp, plano, com_copart)}
    return [n for n in nomes if n.upper() in validos]


def fora_do_padrao(nomes: list[str]) -> list[str]:
    """PDF/XLSX/CSV na pasta que nao segue o padrao (nao vai pro cliente)."""
    return [n for n in nomes if n.lower().endswith(ANEXAVEIS + (".csv",)) and not _RE_PADRAO.match(n.upper())
            and not n.lower().endswith(".sharepoint")]


# ---------------------------------------------------------------- conferencia de valor

_RE_VALOR_OBRIGACAO = re.compile(r"Valor Obrigacao\s+(\d+)")


def _ler_texto(caminho: str) -> str:
    with open(caminho, "rb") as f:
        bruto = f.read()
    try:
        return bruto.decode("utf-8-sig")
    except UnicodeDecodeError:
        return bruto.decode("latin-1")


def _float_br(v: str) -> float | None:
    v = (v or "").strip().strip('"')
    if not v:
        return None
    if "," in v:
        v = v.replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def total_relatorio_mensal(caminho_csv: str) -> float | None:
    """Analitico da mensalidade (saude/odonto): cabecalho 'Valor Obrigacao 3791400' (centavos)."""
    m = _RE_VALOR_OBRIGACAO.search(_ler_texto(caminho_csv))
    return int(m.group(1)) / 100 if m else None


def total_relatorio_copart(caminho_csv: str) -> float | None:
    """Analitico de coparticipacao: soma de VL PARTICIPACAO - VL REVERTIDO das linhas
    (formato 'PLANILHA DE COPARTICIPACAO' da Hapvida, separador ';')."""
    linhas = _ler_texto(caminho_csv).splitlines()
    i_cab = next((i for i, l in enumerate(linhas) if "VL PARTICIPACAO" in l.upper()), None)
    if i_cab is None:
        return None
    cab = [c.strip().upper() for c in linhas[i_cab].split(";")]
    i_part = cab.index("VL PARTICIPACAO")
    i_rev = cab.index("VL REVERTIDO") if "VL REVERTIDO" in cab else None
    total = 0.0
    for linha in csv.reader(io.StringIO("\n".join(linhas[i_cab + 1:])), delimiter=";", quotechar='"'):
        if len(linha) <= i_part:
            continue
        part = _float_br(linha[i_part])
        if part is None:
            continue
        rev = _float_br(linha[i_rev]) if i_rev is not None and len(linha) > i_rev else None
        total += part - (rev or 0.0)
    return round(total, 2)


def conferir_valores(pasta: str, codigo: str, comp: str, plano: str, com_copart: bool,
                     valores: dict[str, set[float]] | set[float]) -> list[str]:
    """Total de cada relatorio analitico x boleto da competencia.

    valores: por plano ({'SAUDE': {37914.0}, 'COPARTICIPACAO': {673.2}}), quando
    se sabe o tipo de cada boleto (portal novo); ou um conjunto unico com todos os
    boletos do mes (portal antigo, status do Cowork nao diz qual e' copart) - ai o
    total tem que bater com um deles. Devolve a lista de problemas (vazia = ok)."""
    problemas = []
    for p in planos_do_contrato(plano, com_copart):
        csv_nome = nome("RELATORIO_ANALITICO", p, codigo, comp, "csv")
        caminho = next((os.path.join(pasta, n) for n in (os.listdir(pasta) if os.path.isdir(pasta) else [])
                        if n.upper() == csv_nome.upper()), None)
        if not caminho:
            continue  # falta da fonte e' cobrada a parte (faltando_fontes)
        total = total_relatorio_copart(caminho) if p == "COPARTICIPACAO" else total_relatorio_mensal(caminho)
        alvo = valores.get(p, set()) if isinstance(valores, dict) else valores
        if total is None:
            problemas.append(f"{csv_nome}: não consegui ler o total do relatório")
        elif not alvo:
            problemas.append(f"{csv_nome}: valor do boleto {p.lower()} de {comp} desconhecido — não dá para conferir")
        elif not any(abs(total - v) <= TOLERANCIA for v in alvo):
            esperado = " ou ".join(f"R$ {v:,.2f}" for v in sorted(alvo))
            problemas.append(f"RELATORIO_ANALITICO_{p} com total R$ {total:,.2f} ≠ boleto do mês ({esperado}) — "
                             "relatório de outro período ou acumulado")
    return problemas
