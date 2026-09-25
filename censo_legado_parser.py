"""
Parser do HTML de get_ativos (webNewCadastroUsuario.Usuario) pro formato
oficial de CSV que bulk_import_beneficiarios_oficial.py espera. Validado
manualmente contra 611X2 e Z8TUR no #56/#59 - 0 rejeitados nos dois.

Estrutura real da tabela (achado no #56): marcador `<tr class="table-header">`
com colunas TITULAR/DEPENDENTE, CPF, MATRICULA, CODIGO, PLANO. Dependentes
vem com prefixo "» " no nome; titulares nao tem prefixo. A hierarquia
titular/dependente vem da ORDEM das linhas (cada dependente pertence ao
titular mais recente que veio antes dele), nao existe coluna explicita
de vinculo.

Requer `lxml` como parser do BeautifulSoup - o `html.parser` padrao do
Python nao navega essa estrutura corretamente (tags mal fechadas no HTML
de origem fazem ele perder linhas).
"""
import csv
import io

from bs4 import BeautifulSoup

FIELDNAMES = [
    "EMPRESA", "CPF_DEPENDENTE", "USUARIO", "MATRICULA", "CPF", "TIPO_USUARIO",
    "PLANO", "DATA_NASCIMENTO", "SEXO", "NOME_MAE", "DT_ADMISSAO", "DT_CANCELAMENTO",
]


def parse_ativos_html(html: str, contrato: str) -> list[dict]:
    """Extrai os registros da tabela de ativos. Levanta ValueError se o
    marcador da tabela nao for encontrado (layout mudou ou sessao invalida)."""
    soup = BeautifulSoup(html, "lxml")
    header_tr = soup.find("tr", class_="table-header")
    if header_tr is None:
        raise ValueError(
            f"Marcador da tabela de ativos nao encontrado pro contrato {contrato} "
            "- layout pode ter mudado, ou sessao invalida/expirada"
        )

    parent_table = header_tr.find_parent("table")
    rows = parent_table.find_all("tr")

    registros = []
    titular_cpf_atual = None

    for r in rows[1:]:  # pula o header
        cells = [c.get_text(" ", strip=True) for c in r.find_all(["td", "th"])]
        if len(cells) < 5 or not cells[0]:
            continue  # linha espacadora vazia
        nome_raw, cpf, matricula, _codigo, plano = cells[0], cells[1], cells[2], cells[3], cells[4]
        if not cpf:
            continue

        is_dependente = nome_raw.startswith("»")
        nome = nome_raw.lstrip("»").strip()

        if is_dependente:
            tipo = "DEPENDENTE"
            cpf_titular = titular_cpf_atual or ""
        else:
            tipo = "TITULAR"
            titular_cpf_atual = cpf
            cpf_titular = cpf

        registros.append({
            "EMPRESA": contrato,
            "CPF_DEPENDENTE": cpf,
            "USUARIO": nome,
            "MATRICULA": matricula,
            "CPF": cpf_titular,
            "TIPO_USUARIO": tipo,
            "PLANO": plano,
            "DATA_NASCIMENTO": "",
            "SEXO": "",
            "NOME_MAE": "",
            "DT_ADMISSAO": "",
            "DT_CANCELAMENTO": "",
        })

    return registros


def registros_para_csv_bytes(registros: list[dict]) -> bytes:
    """Gera o CSV no formato oficial (utf-8-sig, delimitador ;) em memoria."""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDNAMES, delimiter=";", quotechar='"')
    writer.writeheader()
    writer.writerows(registros)
    return ("﻿" + buf.getvalue()).encode("utf-8")
