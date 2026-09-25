"""
Documentos por fatura do Grupo Saude (0KFXW/WARQU + coligadas): Nota Fiscal e
Relatorio Analitico da Fatura Mensal, via API moderna do portal-empresa
(mesma sessao/login de censo_saude.py, escopada por criar_sessao_empresa).

Mapeado ao vivo em 01/08 a partir da tela "Detalhes da fatura"
(financeiro/detalhes/{numero}/unpaid-bill), testando o contrato EM593:
- Nota Fiscal: GET /b2b/api/v3/billings/invoice?billing-number={n}
  Retorna {invoiceNumber, verificationCode, invoicePath} onde invoicePath e
  um link pro portal da prefeitura (nfe.prefeitura.sp.gov.br pra empresas de
  SP - o dominio muda conforme o municipio de cada nota, nao assumir SP fixo).
  Confirmado (EM593): CADA fatura (mensalidade E coparticipacao) tem uma NFe
  PROPRIA, numero diferente - nao e "uma nota por contrato", e por fatura.
  Quando nao ha nota emitida pra aquela fatura, a API pode devolver 404 ou um
  invoicePath vazio/invalido - tratar os dois casos como "sem nota".
- Relatorio Analitico: GET /b2b/api/v3/billings/report-monthly/billing-number/{n}/report-type/{PDF|CSV|TXT}
  Retorna {content: {link, length, type}} com um link SAS do Azure Blob
  Storage, valido por ~1h (query param "se=" e o timestamp de expiracao) -
  baixar na hora, nao guardar o link cru pra depois. Confirmado (EM593):
  funciona pra fatura tipo "MENSALIDADE PLANO SAUDE", mas devolve 404
  "Remessa inexistente" pra fatura tipo "CARNET COMPLEMENTAR-COPARTICIPACAO"
  - e uma limitacao real da Hapvida (o relatorio e gerado a partir da mesma
  remessa/arquivo do censo mensal, que so existe pro ciclo de mensalidade),
  nao um bug daqui. PDF/CSV/TXT sao a mesma remessa em 3 formatos - se um
  existe, os outros tres normalmente tambem existem (nao confirmado 100% dos
  casos, so testado assim no EM593).
- Boleto (arquivo em si, alem do dado ja em `faturas`):
  GET /b2b/api/v3/billings/partner-company/{codigo}/billing-number/{n}/bill
  Devolve o PDF direto (sem link intermediario), sempre que a fatura existe.

IMPORTANTE pro Grupo Legado (quando for mapeado): este modulo NAO se aplica.
Legado usa um portal e dominio completamente diferentes
(webhap.hapvida.com.br / sigo.sh.srv.br via SSO, dependendo do contrato),
login bloqueado por captcha (#77), e o endpoint de nota fiscal la e outro
(pk_nota_fiscal.listaNotasFiscais, testado vazio pros 4 codigos Grupo Saude
tentados via o mesmo mecanismo - Addendum 8). Quando o André passar sessao
manual do Legado, o equivalente a mapear la e: (1) achar se existe uma tela
de "detalhes da fatura" por la com os mesmos 3 documentos, (2) capturar a
URL de rede ao clicar em cada um (Boleto/Nota Fiscal/Relatorio), exatamente
como foi feito aqui - NAO assumir que os mesmos paths funcionam.
"""
import os
import time

import httpx
import psycopg2
from playwright.sync_api import sync_playwright

from censo_saude import API_BASE, _headers_http, criar_sessao_empresa, login_saude

MAX_TENTATIVAS = 3
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


def _get_com_retry(url, headers, params=None):
    ultimo_erro = None
    for _ in range(MAX_TENTATIVAS):
        try:
            r = httpx.get(url, headers=headers, params=params, timeout=_TIMEOUT)
        except httpx.TimeoutException as e:
            ultimo_erro = f"timeout: {e}"
            time.sleep(2)
            continue
        if r.status_code in (200, 404):
            return r
        ultimo_erro = f"{r.status_code}: {r.text[:200]}"
        time.sleep(2)
    raise RuntimeError(f"falhou {MAX_TENTATIVAS}x: {ultimo_erro}")


def checar_nota_fiscal(auth: dict, codigo: str, billing_number: str) -> dict | None:
    """Retorna {invoiceNumber, verificationCode, invoicePath} ou None se nao houver NFe pra essa fatura."""
    criar_sessao_empresa(auth, codigo)
    r = _get_com_retry(f"{API_BASE}/billings/invoice", _headers_http(auth), {"billing-number": billing_number})
    if r.status_code == 404:
        return None
    content = r.json().get("content") or {}
    if not content.get("invoicePath"):
        return None
    return content


def checar_relatorio_analitico(auth: dict, codigo: str, billing_number: str, tipo: str = "PDF") -> dict | None:
    """Retorna {link, length, type} ou None se a remessa nao existir pra essa fatura (ex: coparticipacao)."""
    criar_sessao_empresa(auth, codigo)
    url = f"{API_BASE}/billings/report-monthly/billing-number/{billing_number}/report-type/{tipo}"
    r = _get_com_retry(url, _headers_http(auth))
    if r.status_code == 404:
        return None
    return r.json().get("content")


def baixar_relatorio_analitico(auth: dict, codigo: str, billing_number: str, tipo: str) -> bytes | None:
    info = checar_relatorio_analitico(auth, codigo, billing_number, tipo)
    if not info:
        return None
    r = httpx.get(info["link"], timeout=60)
    r.raise_for_status()
    return r.content


def baixar_boleto_pdf(auth: dict, codigo: str, billing_number: str) -> bytes:
    criar_sessao_empresa(auth, codigo)
    url = f"{API_BASE}/billings/partner-company/{codigo}/billing-number/{billing_number}/bill"
    r = httpx.get(url, headers=_headers_http(auth), timeout=30)
    r.raise_for_status()
    return r.content


def _db():
    return psycopg2.connect(dbname="lifetools")


def listar_faturas_mais_recentes_por_contrato() -> list[dict]:
    """1 fatura (a mais recente por vencimento) por codigo_externo que tenha ao menos 1 fatura -
    amostra representativa pra mapear disponibilidade de documentos por CONTRATO, sem varrer
    as 223 faturas historicas uma a uma."""
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (eoc.codigo_externo)
                    eoc.codigo_externo, f.billing_number, f.numero, f.bill_type, f.vencimento
                FROM faturas f
                JOIN empresa_operadora_codigos eoc ON eoc.id = f.empresa_operadora_codigo_id
                ORDER BY eoc.codigo_externo,
                    (f.bill_type = 'MENSALIDADE PLANO SAUDE') DESC,
                    f.vencimento DESC
                """
            )
            cols = ["codigo_externo", "billing_number", "numero", "bill_type", "vencimento"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def registrar_disponibilidade(billing_number: str, numero: str, nfe: dict | None, relatorio_disponivel: bool) -> None:
    conn = _db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE faturas SET
                    nfe_disponivel = %s,
                    nfe_numero = %s,
                    nfe_codigo_verificacao = %s,
                    relatorio_analitico_disponivel = %s,
                    documentos_verificados_em = NOW()
                WHERE billing_number = %s AND numero = %s
                """,
                (
                    nfe is not None,
                    nfe.get("invoiceNumber") if nfe else None,
                    nfe.get("verificationCode") if nfe else None,
                    relatorio_disponivel,
                    billing_number,
                    numero,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def mapear_disponibilidade_documentos(auth: dict) -> dict:
    """Roda o mapeamento completo: pra cada contrato com ao menos 1 fatura, checa NFe e
    Relatorio Analitico (PDF) na fatura mais recente, grava o resultado em `faturas`."""
    amostra = listar_faturas_mais_recentes_por_contrato()
    resultados = []
    for item in amostra:
        codigo = item["codigo_externo"]
        billing_number = item["billing_number"]
        numero = item["numero"]
        print(f"[documentos_saude] processando {codigo} ({billing_number})...", flush=True)
        try:
            nfe = checar_nota_fiscal(auth, codigo, billing_number)
            relatorio = checar_relatorio_analitico(auth, codigo, billing_number, "PDF")
            registrar_disponibilidade(billing_number, numero, nfe, relatorio is not None)
            print(f"[documentos_saude] {codigo} ok — nfe={nfe is not None} relatorio={relatorio is not None}", flush=True)
            resultados.append(
                {
                    "codigo": codigo,
                    "billing_number": billing_number,
                    "bill_type": item["bill_type"],
                    "status": "ok",
                    "nfe_disponivel": nfe is not None,
                    "relatorio_disponivel": relatorio is not None,
                }
            )
        except Exception as e:
            resultados.append({"codigo": codigo, "billing_number": billing_number, "status": "erro", "detalhe": str(e)})
            print(f"[documentos_saude] {codigo} ERRO — {e}", flush=True)
    return {"total_contratos": len(amostra), "resultados": resultados}


def run_mapear_documentos(usuario: str, senha: str) -> dict:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        try:
            auth = login_saude(page, usuario, senha)
        finally:
            browser.close()
    return mapear_disponibilidade_documentos(auth)
