"""Detalhe por contrato de cada lote de comissão (tabela `comissoes_detalhe`, #155).

Baixa o "Relatório de Comissão Sintético por Corretora" (pk_portal_r61692sc) de cada lote listado no
menu de relatórios do Portal do Corretor e grava uma linha por contrato e vencimento. É o relatório
resumido, sem nome de beneficiário (o detalhado, 61692a, tem PII e não é usado).

Até 21/10/2026 isto só tinha rodado uma vez, à mão (17/09, meses 01 a 09/2026), e o detalhe ficou
parado em 11/09 enquanto os pedidos continuavam chegando. Agora roda dentro do run_all() diário,
na mesma sessão do login de cada código, para o mês atual e o anterior.

Uso avulso (recarga): python3 comissao_detalhe.py <CODIGO> <MM/AAAA> [<MM/AAAA> ...]
"""
import os
import re
import sys
from datetime import date

import psycopg2
from playwright.sync_api import sync_playwright

from scraper import HAPVIDA_OPERADORA_ID, HapvidaAdapter, get_credentials, normalize_valor

LISTAGEM_URL = (
    "https://webhap.hapvida.com.br/pls/webhap/pk_portal_menu_relatorio.listarRelatorio_dyn"
    "?pIdSessao={sessao}&pOpcao=R&pDtRef={mes}"
)
SINTETICO_URL = (
    "https://www.hapvida.com.br/pls/webhap/pk_portal_r61692sc.downloadCSVR61692sc_dyn"
    "?pNoCache=1417282607&pIdSessao={sessao}&pNuReport={nurel}&pCdAreaVenda={area}&pCdComObgIntegra={nota}"
)

RE_REPORT_CALL = re.compile(r"report\((\d+),(\d+),(\d+),'([^']+)',(\d+),'([^']+)',(\d+)\)")
RE_LOTE = re.compile(r"Lote:\s*(\S+)")
RE_TIPO = re.compile(r"Tipo Comissao:\s*(.+?)\s+Regra:", re.S)
RE_REGRA = re.compile(r"Regra:\s*(\S+)")
RE_DATA_INTEG = re.compile(r"Data Integração:\s*(\d{2}/\d{2}/\d{4})")
RE_PERIODO = re.compile(r"Periodo\s*:\s*(\d{2}/\d{2}/\d{4})\s*até\s*(\d{2}/\d{2}/\d{4})")
RE_OBRIG = re.compile(r"Obrigação:\s*(\S+)\s*-\s*R\$\s*([\d\.,]+)")


def meses_recentes(hoje: date, quantos: int = 2) -> list[str]:
    """Mês atual e os anteriores, no formato MM/AAAA da listagem do portal."""
    meses = []
    ano, mes = hoje.year, hoje.month
    for _ in range(quantos):
        meses.append(f"{mes:02d}/{ano}")
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    return meses


def _data_br(s: str) -> str:
    d, m, a = s.split("/")
    return f"{a}-{m}-{d}"


def parse_bloco(texto: str) -> dict | None:
    m_lote = RE_LOTE.search(texto)
    m_obrig = RE_OBRIG.search(texto)
    if not (m_lote and m_obrig):
        return None
    m_tipo = RE_TIPO.search(texto)
    m_regra = RE_REGRA.search(texto)
    m_data = RE_DATA_INTEG.search(texto)
    m_periodo = RE_PERIODO.search(texto)
    return {
        "lote": m_lote.group(1),
        "tipo_comissao": m_tipo.group(1).strip() if m_tipo else None,
        "regra": m_regra.group(1) if m_regra else None,
        "data_integracao": _data_br(m_data.group(1)) if m_data else None,
        "periodo_inicio": _data_br(m_periodo.group(1)) if m_periodo else None,
        "periodo_fim": _data_br(m_periodo.group(2)) if m_periodo else None,
        "obrigacao": m_obrig.group(1),
        "valor_lote": normalize_valor(m_obrig.group(2)),
    }


def parse_csv_sintetico(conteudo: bytes) -> list[dict]:
    linhas = [l for l in conteudo.decode("latin1").splitlines() if l.strip()]
    if not linhas:
        return []
    header = [h.strip() for h in linhas[0].split(";")]
    idx = {h: i for i, h in enumerate(header)}
    saida = []
    for linha in linhas[1:]:
        cols = linha.split(";")
        if len(cols) < len(header):
            continue

        def campo(nome):
            return cols[idx[nome]].strip()

        def num(nome):
            v = campo(nome)
            return float(v.replace(",", ".")) if v else None

        empresa = campo("EMPRESA CONVENIADA")
        codigo_contrato, nome_empresa = empresa.split(" - ", 1) if " - " in empresa else (empresa, empresa)
        saida.append(
            {
                "cod_comissionado": campo("COD. COMISSIONADO"),
                "nome_comissionado": campo("NOME COMISSIONADO"),
                "codigo_contrato": codigo_contrato.strip(),
                "nome_empresa": nome_empresa.strip(),
                "vencimento": _data_br(campo("VENCIMENTO")),
                "parcela": int(campo("PARCELA(E)")) if campo("PARCELA(E)") else None,
                "vl_base_com": num("VL BASE COM.(A)"),
                "pct_imposto": num("% IMP."),
                "vl_liquido": num("Vl LIQUIDO"),
                "pc_dist": num("PC. DIST"),
                "vl_a_pagar": num("VL A PAGAR"),
            }
        )
    return saida


def upsert_detalhe(conn, codigo: str, bloco: dict, linhas: list[dict], fonte: str) -> None:
    with conn.cursor() as cur:
        for l in linhas:
            cur.execute(
                """
                INSERT INTO comissoes_detalhe
                    (operadora_id, codigo_concessionaria, lote, tipo_comissao, regra,
                     data_integracao, periodo_inicio, periodo_fim, obrigacao, valor_lote,
                     cod_comissionado, nome_comissionado, codigo_contrato, nome_empresa,
                     vencimento, parcela, vl_base_com, pct_imposto, vl_liquido, pc_dist,
                     vl_a_pagar, fonte)
                VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s)
                ON CONFLICT (operadora_id, codigo_concessionaria, lote, codigo_contrato, vencimento)
                DO UPDATE SET
                    tipo_comissao=EXCLUDED.tipo_comissao, regra=EXCLUDED.regra,
                    data_integracao=EXCLUDED.data_integracao, periodo_inicio=EXCLUDED.periodo_inicio,
                    periodo_fim=EXCLUDED.periodo_fim, obrigacao=EXCLUDED.obrigacao,
                    valor_lote=EXCLUDED.valor_lote, parcela=EXCLUDED.parcela,
                    vl_base_com=EXCLUDED.vl_base_com, pct_imposto=EXCLUDED.pct_imposto,
                    vl_liquido=EXCLUDED.vl_liquido, pc_dist=EXCLUDED.pc_dist,
                    vl_a_pagar=EXCLUDED.vl_a_pagar, coletado_em=now(), fonte=EXCLUDED.fonte
                """,
                (
                    HAPVIDA_OPERADORA_ID, codigo, bloco["lote"], bloco["tipo_comissao"], bloco["regra"],
                    bloco["data_integracao"], bloco["periodo_inicio"], bloco["periodo_fim"],
                    bloco["obrigacao"], bloco["valor_lote"],
                    l["cod_comissionado"], l["nome_comissionado"], l["codigo_contrato"], l["nome_empresa"],
                    l["vencimento"], l["parcela"], l["vl_base_com"], l["pct_imposto"], l["vl_liquido"],
                    l["pc_dist"], l["vl_a_pagar"], fonte,
                ),
            )
    conn.commit()


def baixar_sintetico(ctx, sessao: str, onclick: str) -> bytes:
    m = RE_REPORT_CALL.search(onclick or "")
    if not m:
        raise ValueError(f"botão Excel não reconhecido: {onclick}")
    _tipo, _modelo, nurel, area, _dtr, _comissionado, nota = m.groups()
    p2 = ctx.new_page()
    try:
        with p2.expect_download(timeout=15000) as dl:
            try:
                p2.goto(SINTETICO_URL.format(sessao=sessao, nurel=nurel, area=area, nota=nota), timeout=10000)
            except Exception:
                pass  # o portal responde com download, o goto "falha" por isso
        with open(dl.value.path(), "rb") as f:
            return f.read()
    finally:
        p2.close()


def coletar(page, sessao: str, conn, codigo: str, meses: list[str]) -> dict:
    """Coleta o detalhe de todos os lotes dos meses pedidos, numa sessão já logada no código."""
    resumo = {"lotes_ok": 0, "linhas": 0, "lotes_sem_sintetico": 0, "lotes_divergentes": [], "erros": []}
    for mes in meses:
        try:
            page.goto(LISTAGEM_URL.format(sessao=sessao, mes=mes))
            page.wait_for_load_state("networkidle")
        except Exception as e:
            resumo["erros"].append(f"{mes}: listagem não carregou ({e})")
            continue
        if "EXPIROU" in page.content().upper():
            resumo["erros"].append(f"{mes}: sessão expirou na listagem")
            continue

        blocos = page.locator("tr:has-text('Obrigação')")
        for i in range(blocos.count()):
            bloco = parse_bloco(blocos.nth(i).inner_text())
            if not bloco:
                continue
            linha_sint = blocos.nth(i).locator("xpath=following-sibling::tr[1]")
            botao = linha_sint.locator('input[value="Excel"]')
            if "Sintético por Corretora" not in linha_sint.inner_text() or botao.count() == 0:
                resumo["lotes_sem_sintetico"] += 1
                continue
            try:
                linhas = parse_csv_sintetico(baixar_sintetico(page.context, sessao, botao.get_attribute("onclick")))
            except Exception as e:
                resumo["erros"].append(f"lote {bloco['lote']}: download falhou ({e})")
                continue
            if not linhas:
                resumo["erros"].append(f"lote {bloco['lote']}: CSV vazio")
                continue

            soma = round(sum(l["vl_a_pagar"] or 0 for l in linhas), 2)
            if bloco["valor_lote"] is not None and abs(soma - bloco["valor_lote"]) > 0.02:
                resumo["lotes_divergentes"].append(
                    {"lote": bloco["lote"], "regra": bloco["regra"], "soma": soma, "valor_lote": bloco["valor_lote"]}
                )
            upsert_detalhe(conn, codigo, bloco, linhas, fonte=f"hapvida_portal_corretor_{mes.replace('/', '')}")
            resumo["lotes_ok"] += 1
            resumo["linhas"] += len(linhas)
    return resumo


if __name__ == "__main__":
    codigo, meses = sys.argv[1].upper(), sys.argv[2:]
    creds = get_credentials(os.environ["GCP_PROJECT"], f"hapvida-{codigo.lower()}")
    conn = psycopg2.connect(dbname="lifetools")
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_context(accept_downloads=True).new_page()
            sessao = HapvidaAdapter(page).login(creds["usuario"], creds["senha"])
            print(f"{codigo}: {coletar(page, sessao, conn, codigo, meses)}")
            browser.close()
    finally:
        conn.close()
