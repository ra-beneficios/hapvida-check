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
    """CSV oficial que falta. Copart (Andre, 17/11/2026): sem o CSV (a Hapvida gera em fila),
    o PDF oficial da fatura e' fonte - o XLSX sai dele (copart_pdf_para_linhas) e o total
    dele e' conferido com o boleto."""
    tem = _presentes(nomes)
    copart_pdf = nome("RELATORIO_ANALITICO", "COPARTICIPACAO", codigo, comp, "pdf").upper()
    copart_csv = nome("RELATORIO_ANALITICO", "COPARTICIPACAO", codigo, comp, "csv").upper()
    return [e for e in fontes_csv(codigo, comp, plano, com_copart)
            if e.upper() not in tem and not (e.upper() == copart_csv and copart_pdf in tem)]


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
        if not caminho and p == "COPARTICIPACAO":
            # sem o CSV, o PDF oficial da fatura (17/11/2026): total = soma das linhas lidas
            pdf_nome = nome("RELATORIO_ANALITICO", p, codigo, comp, "pdf")
            caminho = next((os.path.join(pasta, n) for n in (os.listdir(pasta) if os.path.isdir(pasta) else [])
                            if n.upper() == pdf_nome.upper()), None)
            if caminho:
                csv_nome = pdf_nome
                try:
                    total = copart_pdf_para_linhas(caminho)["total"]
                except ValueError as e:
                    total = None
                    problemas.append(f"{pdf_nome}: {e}")
                    continue
        elif caminho:
            total = total_relatorio_copart(caminho) if p == "COPARTICIPACAO" else total_relatorio_mensal(caminho)
        if not caminho:
            continue  # falta da fonte e' cobrada a parte (faltando_fontes)
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


# ---------------------------------------------------------------- copart pelo PDF (17/11/2026)

COLUNAS_COPART_PDF = ["Titular", "Cód. Usuário", "Matrícula", "CPF", "Beneficiário", "Guia", "Data atendimento",
                      "Data (2ª)", "Cód. procedimento", "Procedimento", "Prestador", "Qtd", "Valor (R$)"]
_RE_CP_FATURA = re.compile(r"^\s*(\d{9,})\s+(\d\d/\d\d/\d{4})\s+(-?[\d.]+,\d\d)\s*$")
_RE_CP_PROC = re.compile(r"^\s*(\S+)\s+(\d\d/\d\d/\d{4})\s+(\d\d/\d\d/\d{4})\s+(\S+) - (.+?)\s{2,}(.+?)\s{2,}"
                         r"(\d+)\s+(-?[\d.]+,\d\d)\s*$")
_RE_CP_TOTAL = re.compile(r"^\s*Total:\s+(-?[\d.]+,\d\d)\s*$")
_RE_CP_TITULAR = re.compile(r"^\s*([A-Z0-9]{5,})-(\S.*)$")
_CP_CABECALHO_PAGINA = ("NOTRE DAME", "SISTEMA DE CONTROLE", "FATURA DE CO-PARTICIP", "HAPVIDA")


def copart_pdf_para_linhas(caminho_pdf: str) -> dict:
    """Le o PDF oficial 'FATURA DE CO-PARTICIPACAO' do portal novo (o mesmo do botao
    'Relatorio de coparticipacao' da fatura) e devolve {'cabecalho': [3 linhas],
    'fatura', 'vencimento', 'valor', 'linhas': [13 colunas de COLUNAS_COPART_PDF], 'total'}.
    Falha segura (ValueError): linha que nao reconhece, beneficiario sem cabecalho, ou soma
    das linhas diferente do valor da fatura impresso no PDF ou dos subtotais 'Total:'.
    Criado 17/11/2026 (Andre, UTC DAVJV NOV26: CSV em fila na Hapvida, o PDF ja' estava pronto)."""
    from datetime import datetime
    import pypdf
    paginas = [p.extract_text(extraction_mode="layout") or "" for p in pypdf.PdfReader(caminho_pdf).pages]
    empresa, fatura, linhas, subtotais = None, None, [], []
    titular, usuario, espera_usuario, col_desc, col_prestador = None, None, False, None, None
    for texto in paginas:
        for l in texto.splitlines():
            s = l.strip()
            if not s or s.upper().startswith(_CP_CABECALHO_PAGINA) or "PAGINA -" in s.upper():
                continue
            if s.startswith("Cod. Usuario"):
                espera_usuario = True
                continue
            if espera_usuario:
                espera_usuario = False
                partes = re.split(r"\s{2,}", s)
                cpf = next((t for t in partes[1:] if re.fullmatch(r"\d{11}", t)), "")
                meio = [t for t in partes[1:-1] if t != cpf]
                usuario = (partes[0], meio[0] if meio else "", cpf, partes[-1] if len(partes) > 1 else "")
                continue
            m = _RE_CP_FATURA.match(l)
            if m:
                if fatura and fatura[0] != m.group(1):
                    raise ValueError(f"PDF com duas faturas ({fatura[0]} e {m.group(1)})")
                fatura = m.groups()
                continue
            m = _RE_CP_PROC.match(l)
            if m:
                if not usuario:
                    raise ValueError(f"atendimento sem beneficiário: {s[:80]}")
                g = m.groups()
                linhas.append([titular, *usuario, g[0], datetime.strptime(g[1], "%d/%m/%Y"),
                               datetime.strptime(g[2], "%d/%m/%Y"), g[3], g[4], g[5], int(g[6]), _float_br(g[7])])
                col_desc, col_prestador = m.start(4), m.start(6)  # a quebra alinha com o codigo do procedimento
                continue
            m = _RE_CP_TOTAL.match(l)
            if m:
                subtotais.append(_float_br(m.group(1)))
                continue
            if s == empresa:
                continue  # repete no topo de cada pagina
            if empresa is None and _RE_CP_TITULAR.match(l):
                empresa = s
                continue
            if _RE_CP_TITULAR.match(l):
                if s != titular:  # titular repetido no topo da pagina seguinte: mesmo beneficiario
                    titular, usuario = s, None
                continue
            inicio = len(l) - len(l.lstrip())
            if linhas and col_desc is not None and inicio >= col_desc - 6:
                # descricao/prestador quebrado em 2 linhas no PDF (colado: a Hapvida corta no meio da palavra)
                linhas[-1][10 if inicio >= col_prestador - 6 else 9] += s
                continue
            raise ValueError(f"linha que não sei ler no PDF de coparticipação: {s[:80]}")
    if not fatura or not linhas:
        raise ValueError("PDF sem fatura ou sem atendimentos (não é o relatório de coparticipação do portal novo)")
    total = round(sum(x[12] for x in linhas), 2)
    valor = _float_br(fatura[2])
    if abs(total - valor) > TOLERANCIA or abs(round(sum(subtotais), 2) - total) > TOLERANCIA:
        raise ValueError(f"soma das linhas R$ {total:,.2f} não bate com a fatura (R$ {valor:,.2f}) "
                         f"ou com os subtotais (R$ {sum(subtotais):,.2f})")
    return {"cabecalho": ["FATURA DE CO-PARTICIPAÇÃO", empresa or "",
                          f"Fatura {fatura[0]} · vencimento {fatura[1]} · valor R$ {fatura[2]}"],
            "fatura": fatura[0], "vencimento": fatura[1], "valor": valor, "linhas": linhas, "total": total}
