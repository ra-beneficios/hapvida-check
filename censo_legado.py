"""
Scraper do censo para o grupo legado (Odonto Premium + DAVJV + Z8TUR).
Login via Playwright (1x por contrato) -> extrai pPessoa/pIdSessao ->
todas as chamadas de dado seguintes sao GET direto via requests/httpx,
sem precisar manter o browser aberto. Validado manualmente no #54/#56
(23-25/07/2026) para os contratos 611X2 e Z8TUR.

run_censo_legado() produtiza o fluxo completo: login -> 3 fetches ->
parse do HTML de ativos -> CSV temporario em memoria -> dry-run do
bulk_import_beneficiarios_oficial.py -> limpeza. NAO commita nada
automaticamente (ver app.py / regra de auto-commit em auto_commit.py).
"""
import os
import re
import subprocess
import tempfile
import time

import httpx
from playwright.sync_api import sync_playwright

import censo_legado_parser

RATE_LIMIT_SECONDS = 8  # mesmo intervalo do hapvida-check, evita pMsg=008

LOGIN_URL = "https://webhap.hapvida.com.br/pls/webhap/webnewcadastrousuario.login"

# 12 Odonto Premium + DAVJV + Z8TUR (confirmado via
# empresa_operadora_codigos WHERE modalidade='ODONTO_PREMIUM' OR
# codigo_externo IN ('DAVJV','Z8TUR'), 25/07/2026)
CONTRATOS_LEGADO = [
    "2M2DX", "32I14", "611X2", "BUZ7F", "BY3S8", "BZ3T5", "DAVJV",
    "HH9PR", "IHWBW", "QTYQH", "S7HNK", "SY8MI", "Y4VLX", "Z8TUR",
]


def login_legado(page, codigo: str, senha: str) -> dict:
    """
    Faz login no portal legado pra 1 contrato, usando 1 `page` do
    Playwright (mesmo padrao de isolamento do hapvida-check: 1 browser,
    1 page nova por contrato, fechada no finally).

    Mapeamento de campos CONFIRMADO no #54 (nao usar o mapeamento antigo,
    que gerava "NOME DE USUARIO E SENHA NAO CONFEREM"):
      - pCodigoEmpresa = codigo do contrato
      - pNm_Login      = EM BRANCO (nao preencher)
      - pSenha         = senha do secret (so fica visivel no DOM depois
                          que pCodigoEmpresa perde foco - dar Tab e
                          esperar o campo aparecer antes de preencher)
      - pSenhaUnidade  = EM BRANCO

    Retorna {"pPessoa": ..., "pIdSessao": ..., "url_pos_login": ...}.
    Levanta ValueError se a pagina pos-login mostrar "NAO CONFEREM".
    """
    page.goto(LOGIN_URL)
    page.wait_for_load_state("networkidle")
    page.fill("input[name='pCodigoEmpresa']", codigo)
    page.keyboard.press("Tab")
    page.wait_for_selector("input[name='pSenha']", state="visible", timeout=10000)
    page.fill("input[name='pSenha']", senha)
    # pNm_Login e pSenhaUnidade ficam em branco de proposito
    page.click("#Prosseguir")
    page.wait_for_load_state("networkidle")

    url = page.url
    content = page.content()
    if "NÃO CONFEREM" in content or "NAO CONFEREM" in content:
        raise ValueError(f"Login falhou pro contrato {codigo} - credencial ou mapeamento errado")

    m_pessoa = re.search(r"pPessoa=(\d+)", content)
    m_sessao = re.search(r"pIdSessao=([^&\"']+)", url)
    if not m_sessao:
        raise ValueError(f"Login pareceu OK mas nao achei pIdSessao na URL: {url}")

    return {
        "pPessoa": m_pessoa.group(1) if m_pessoa else None,
        "pIdSessao": m_sessao.group(1),
        "url_pos_login": url,
    }


def get_ativos(sessao: dict) -> str:
    """Lista de usuarios/beneficiarios ativos. Marcadores esperados no
    HTML: ATIVO, USUARIO, NOME, CPF (confirmado no #54/#56)."""
    ts = int(time.time() * 1000)
    url = (f"https://www.hapvida.com.br/pls/webhap/"
           f"webNewCadastroUsuario.Usuario?pPessoa={sessao['pPessoa']}"
           f"&pNoCache={ts}&pIdSessao={sessao['pIdSessao']}")
    r = httpx.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def get_movimentacoes(sessao: dict) -> str:
    """Movimentacoes (novos pedidos, cancelamentos, alteracoes, troca de
    unidade). Marcadores esperados: MOVIMENTA, BENEFICI, NOME."""
    ts = int(time.time() * 1000)
    url = (f"https://www.hapvida.com.br/pls/webhap/"
           f"webNewCadastroUsuario.SistemaMenu?pPessoa={sessao['pPessoa']}"
           f"&pNoCache={ts}&pIdSessao={sessao['pIdSessao']}")
    r = httpx.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def get_nota_fiscal(sessao: dict) -> str:
    """Nota fiscal / boletos. Marcadores esperados: NOTA FISCAL, VALOR."""
    ts = int(time.time() * 1000)
    url = (f"https://webhap.hapvida.com.br/pls/webhap/"
           f"pk_nota_fiscal.listaNotasFiscais?pNoCache={ts}"
           f"&pIdSessao={sessao['pIdSessao']}")
    r = httpx.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def _rodar_loader_dry_run(csv_bytes: bytes, timeout: int = 120) -> dict:
    """Salva o CSV num arquivo temporario, roda o loader oficial em
    dry-run (SEM --commit), apaga o arquivo, devolve o resumo parseado -
    incluindo a lista detalhada de rejeitados (identificador/motivo),
    nao so a contagem (achado no #63)."""
    fd, path = tempfile.mkstemp(suffix=".csv", prefix="censo_legado_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(csv_bytes)

        r = subprocess.run(
            ["python3", "/app/bulk_import_beneficiarios_oficial.py", path],
            capture_output=True, text=True, timeout=timeout,
        )
        saida = r.stdout

        def _extrai(rotulo):
            # rotulo pode ser seguido de texto entre parenteses antes do ":"
            # (ex: "Gravados (movimento novo): 3") - casa ate o primeiro ":".
            m = re.search(rf"^{re.escape(rotulo)}[^:\n]*:\s*(\d+)", saida, re.MULTILINE)
            return int(m.group(1)) if m else None

        motivos = re.findall(r"^  - (.+?): (.+)$", saida, re.MULTILINE)

        return {
            "linhas_csv": _extrai("Linhas no CSV"),
            "puladas": _extrai("Puladas"),
            "gravados": _extrai("Gravados"),
            "atualizados": _extrai("Atualizados"),
            "sem_mudanca": _extrai("Sem mudança"),
            "rejeitados": _extrai("Rejeitados"),
            "rejeitados_detalhe": [{"identificador": ident, "motivo": motivo} for ident, motivo in motivos],
            "returncode": r.returncode,
            "stderr": r.stderr[-500:] if r.returncode != 0 else None,
        }
    finally:
        if os.path.exists(path):
            os.remove(path)


def run_censo_legado(project: str, contratos: list[str]) -> dict:
    import scraper as hapvida_scraper

    resultados = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for i, codigo in enumerate(contratos):
                if i > 0:
                    time.sleep(RATE_LIMIT_SECONDS)
                page = browser.new_page()
                try:
                    creds = hapvida_scraper.get_credentials(project, f"hapvida-{codigo}")
                    sessao = login_legado(page, codigo, creds["senha"])
                    ativos_html = get_ativos(sessao)
                    mov_html = get_movimentacoes(sessao)
                    nf_html = get_nota_fiscal(sessao)

                    registros = censo_legado_parser.parse_ativos_html(ativos_html, codigo)
                    csv_bytes = censo_legado_parser.registros_para_csv_bytes(registros)
                    dry_run = _rodar_loader_dry_run(csv_bytes)

                    resultados.append({
                        "codigo": codigo,
                        "status": "ok",
                        "pPessoa": sessao["pPessoa"],
                        "ativos_len": len(ativos_html),
                        "mov_len": len(mov_html),
                        "nf_len": len(nf_html),
                        "registros_parseados": len(registros),
                        "dry_run": dry_run,
                    })
                except Exception as e:
                    resultados.append({"codigo": codigo, "status": "erro", "detalhe": str(e)})
                finally:
                    page.close()
        finally:
            browser.close()
    return {"resultados": resultados}
