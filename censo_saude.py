"""
Scraper do censo pro grupo saude (0KFXW/WARQU + coligadas), via API
moderna do portal-empresa (Azure AD B2C + api.hapvida.com.br). Validado
manualmente no #56 Parte B e #59 para o contrato WARQU com type=Full.

Fluxo: login via Playwright (1x, captura Authorization Bearer +
Ocp-Apim-Subscription-Key via interceptacao de rede) -> POST
create-session pra escopar a mae -> GET beneficiaries/report (dispara
geracao assincrona) -> poll em beneficiaries/status-report ate
"Processed" -> GET no downloadLink (SAS do Azure Blob, sem header extra)
-> CSV no formato oficial, pronto pro loader.

MAES_SAUDE: as 2 empresas mae confirmadas na arvore da Carol (#60) -
"Completo" (type=Full) numa mae baixa a arvore inteira (mae + coligadas)
num arquivo so, testado e confirmado no #59 pra WARQU.
"""
import time

import httpx
from playwright.sync_api import sync_playwright

LOGIN_URL = "https://portal-empresa.hapvidagndi.com.br"
API_BASE = "https://api.hapvida.com.br/b2b/api/v3"

# Tentativa em 31/07 de incluir "DAVJV" aqui foi revertida: a API create-session
# devolveu 400 Bad Request pra DAVJV como partner-company — confirmado em
# fonte_atualizacoes que XFP6J/SY8MI (códigos da DAVJV) já estavam corretamente
# documentados como "Censo Grupo Legado — sessão manual (portal legado, captcha)",
# um sistema DIFERENTE deste (WARQU/0KFXW usam a API moderna, sem captcha). DAVJV não
# pertence a este fluxo — a captura dela depende do portal legado, bloqueado por
# captcha (#77), não é algo que dá pra automatizar por aqui.
MAES_SAUDE = ["WARQU", "0KFXW"]

POLL_INTERVAL_SECONDS = 3
POLL_TIMEOUT_SECONDS = 240  # 0KFXW (mãe grande, ~50 coligadas) passou de 90s no #59


class RelatorioTimeoutError(Exception):
    pass


def login_saude(page, usuario: str, senha: str) -> dict:
    try:
        import portal_vez
        portal_vez.registrar("LOGIN")   # obra 3 (observando)
    except Exception:
        pass
    """Login via Azure B2C (#email/#password/#next, confirmado no #54/#56).
    Captura Authorization e Ocp-Apim-Subscription-Key da primeira chamada
    autenticada pra api.hapvida.com.br, pra reaproveitar via httpx depois
    sem manter o Playwright aberto."""
    headers_capturados = {}

    def on_request(request):
        if "api.hapvida.com.br" in request.url and "authorization" not in headers_capturados:
            h = request.headers
            if h.get("authorization"):
                headers_capturados["authorization"] = h.get("authorization")
                headers_capturados["ocp-apim-subscription-key"] = h.get("ocp-apim-subscription-key")

    page.on("request", on_request)
    page.goto(LOGIN_URL, timeout=25000)
    page.wait_for_load_state("networkidle", timeout=15000)
    page.fill("#email", usuario)
    page.fill("#password", senha)
    page.click("#next")

    for _ in range(10):
        page.wait_for_timeout(1500)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
            if "authorization" in headers_capturados:
                break
        except Exception:
            continue

    if "authorization" not in headers_capturados:
        raise ValueError("Login pareceu OK mas não capturei o header Authorization de nenhuma chamada de API")

    return headers_capturados


def _headers_http(auth: dict) -> dict:
    return {
        "Authorization": auth["authorization"],
        "Ocp-Apim-Subscription-Key": auth["ocp-apim-subscription-key"],
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }


def criar_sessao_empresa(auth: dict, partner_company: str) -> None:
    """POST create-session - escopa as chamadas seguintes pra essa
    empresa/coligada (confirmado no #56 Parte B ao navegar pro contrato)."""
    url = f"{API_BASE}/users/partner-company/{partner_company}/create-session"
    r = httpx.post(url, headers=_headers_http(auth), timeout=30)
    r.raise_for_status()


def gerar_relatorio(auth: dict, partner_company: str, tipo: str = "Full") -> None:
    url = f"{API_BASE}/beneficiaries/report?type={tipo}&movement-flag=false"
    r = httpx.get(url, headers=_headers_http(auth), timeout=30)
    r.raise_for_status()


def _poll_status(auth: dict, tipo: str, link_anterior: str | None = None) -> str:
    url = f"{API_BASE}/beneficiaries/status-report?type={tipo}"
    inicio = time.time()
    while time.time() - inicio < POLL_TIMEOUT_SECONDS:
        r = httpx.get(url, headers=_headers_http(auth), timeout=30)
        r.raise_for_status()
        body = r.json()
        status = body.get("content", {}).get("status")
        link = body.get("content", {}).get("downloadLink")
        if status == "Processed" and link != link_anterior:
            if not link:
                raise ValueError(f"status Processed mas sem downloadLink: {body}")
            return link
        time.sleep(POLL_INTERVAL_SECONDS)
    raise RelatorioTimeoutError(f"Relatório tipo={tipo} não ficou pronto em {POLL_TIMEOUT_SECONDS}s")


def _esperar_fila_livre(auth: dict, tipo: str) -> str | None:
    """Espera nenhum relatorio estar em processamento e devolve o link do
    ultimo pronto - o pedido novo so' vale quando aparecer um link diferente."""
    url = f"{API_BASE}/beneficiaries/status-report?type={tipo}"
    inicio = time.time()
    while time.time() - inicio < POLL_TIMEOUT_SECONDS:
        r = httpx.get(url, headers=_headers_http(auth), timeout=30)
        r.raise_for_status()
        content = r.json().get("content", {})
        if content.get("status") != "Processing":
            return content.get("downloadLink")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise RelatorioTimeoutError(f"Relatório anterior tipo={tipo} ainda em processamento após {POLL_TIMEOUT_SECONDS}s")


def baixar_relatorio(partner_company: str, auth: dict, tipo: str = "Full") -> bytes:
    """Fluxo completo pra 1 empresa: cria sessão, dispara geração,
    espera ficar pronto, baixa o CSV (link SAS, sem header extra)."""
    criar_sessao_empresa(auth, partner_company)
    # O status do relatorio e' por usuario, nao por empresa (achado 01/10/2026:
    # o pedido de AT2BC devolveu o relatorio de KGBQY que ainda estava em
    # processamento). Espera o anterior terminar antes de pedir o novo.
    link_anterior = _esperar_fila_livre(auth, tipo)
    gerar_relatorio(auth, partner_company, tipo)
    download_link = _poll_status(auth, tipo, link_anterior)
    r = httpx.get(download_link, timeout=60)
    r.raise_for_status()
    return r.content


def run_censo_saude(usuario: str, senha: str, maes: list[str] = None) -> dict:
    maes = maes or MAES_SAUDE
    resultados = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        try:
            auth = login_saude(page, usuario, senha)
        finally:
            browser.close()

    for codigo in maes:
        try:
            csv_bytes = baixar_relatorio(codigo, auth, tipo="Full")
            resultados.append({"codigo": codigo, "status": "ok", "csv_len": len(csv_bytes), "csv_bytes": csv_bytes})
        except Exception as e:
            resultados.append({"codigo": codigo, "status": "erro", "detalhe": str(e)})

    return {"resultados": resultados}
