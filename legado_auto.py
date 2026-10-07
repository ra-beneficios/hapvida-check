"""
Faturamento do Legado (portal antigo webhap) com login automatico - assume o
que era do Cowork (Andre, 07/10/2026: "libero o login automatico").

Credencial: cadastrada pelo Andre no Bitwarden (1 item por contrato, com as
URLs dos 4 portais) e copiada pro cofre de maquina pelo
sync_bitwarden_legado.py (host) como segredo `hapvida-legado`:
{CODIGO: {"senha": ..., "cnpj": ...}}. Contrato sem credencial continua com o
Cowork (handoff de sempre).

Por contrato, 4 logins (validados 07/10/2026 com 2M2DX e BY3S8 contra os
arquivos de OUT26 do Cowork - analitico identico byte a byte, boleto e NF com
o mesmo conteudo, lista de ativos batendo):
- webNewBoletoEmpresa (codigo + senha): cai em obrigacaoListaBemobi; o
  portal abre um modal "Evite fraude digital" por cima do botao (fechar
  antes). No iframe Bemobi, "Gerenciar" lista TODAS as faturas em aberto,
  cada uma com "Baixar fatura em PDF" - baixa todas e escolhe a da
  competencia pelo Nosso Numero dentro do PDF. Quem esta em aberto vira
  "Em Aberto" no status; o resto, "Liquidado".
- pk_nota_fiscal (codigo + senha): lista de boletos/NF (12 meses, fora de
  ordem) -> DANFSe na prefeitura de Fortaleza (documentos_legado).
- webNewTrocaArquivo (CNPJ + senha; CNPJ com zfill(14), o Excel come o zero):
  remessa mais recente do analitico (PDF+CSV). So' grava se o cabecalho
  "com vencimento em" for da competencia.
- webnewcadastrousuario (codigo + senha): tem reCAPTCHA v3 invisivel, que
  aprova o navegador normal - so' precisa esperar a URL com pIdSessao.
  Lista de ativos -> CSV (censo_legado_parser).

Por enquanto so' Odonto Premium. Os 3 Saude do Legado (Z8TUR, ERQ43, ICBJY)
tem 2 boletos (mensalidade + copart) e analitico de copart, ainda nao
mapeados aqui - seguem com o Cowork.

Grava com os nomes do padrao NOV26 (padrao_faturamento) e so' o que falta na
pasta. XLSX/PDF derivados dos CSVs saem do gerar_xlsx_relatorios de sempre.
"""
import csv
import io
import os
import re
import time
from datetime import date, datetime

import pypdf
from playwright.sync_api import sync_playwright

import censo_legado
import censo_legado_parser
import documentos_legado
import padrao_faturamento as pf

BASE_URL = "https://webhap.hapvida.com.br/pls/webhap/"
CNPJ_PRESTADOR_FORTALEZA = "63554067000198"
PAUSA = censo_legado.RATE_LIMIT_SECONDS
COLUNAS_STATUS = ["Codigo", "Empresa", "TipoDocumento", "NumeroDocumento", "Vencimento", "Valor", "Status", "DataConsulta"]


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] legado: {msg}", flush=True)


def _texto_pdf(dados: bytes) -> str:
    return "".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(dados)).pages)


def _gravar(pasta: str, nome: str, dados: bytes) -> None:
    os.makedirs(pasta, exist_ok=True)
    tmp = os.path.join(pasta, f".{nome}.tmp")
    with open(tmp, "wb") as f:
        f.write(dados)
    os.replace(tmp, os.path.join(pasta, nome))


def _login(page, portal: str, campos: list[tuple[str, str]]) -> str:
    page.goto(BASE_URL + portal + ".login", timeout=30000)
    page.wait_for_load_state("networkidle")
    for seletor, valor in campos:
        page.fill(seletor, valor)
        page.keyboard.press("Tab")
    page.wait_for_timeout(800)
    modal = page.locator("text=Fechar").first
    if modal.is_visible():
        modal.click()
        page.wait_for_timeout(500)
    page.click("input[type=submit]")
    page.wait_for_load_state("networkidle", timeout=30000)
    if page.locator("input[name=pSenha]").count():  # continua no formulario = recusado
        raise ValueError(f"login recusado em {portal}")
    return page.url


# ---------------------------------------------------------------- portais

def boletos_em_aberto(page, codigo: str, senha: str) -> dict[str, bytes]:
    """Nosso Numero -> PDF de cada fatura em aberto no Bemobi."""
    _login(page, "webNewBoletoEmpresa", [("input[name=pCodigo]", codigo), ("input[name=pSenha]", senha)])
    page.wait_for_timeout(3000)
    frame = page.frame_locator("iframe")
    frame.locator("text=Gerenciar").click(timeout=15000)
    page.wait_for_timeout(1500)
    botoes = frame.locator('[aria-label="Baixar fatura em PDF"]')
    out = {}
    for i in range(botoes.count()):
        with page.expect_download(timeout=20000) as d:
            botoes.nth(i).click()
        with open(d.value.path(), "rb") as f:
            pdf = f.read()
        m = re.search(r"N\.Num\.:\s*(\d{10,})", _texto_pdf(pdf))
        out[m.group(1) if m else f"sem_numero_{i}"] = pdf
        page.wait_for_timeout(1000)
    return out


def lista_notas(page, codigo: str, senha: str) -> list[dict]:
    url = _login(page, "pk_nota_fiscal", [("input[name=pCodigoEmpresa]", codigo), ("input[name=pSenha]", senha)])
    if "listaNotasFiscais" not in url:
        m = re.search(r'href="([^"]*listaNotasFiscais[^"]*)"', page.content())
        if not m:
            raise ValueError("nao achei a lista de notas fiscais depois do login")
        url = BASE_URL + m.group(1).split("/")[-1].replace("&amp;", "&")
    return documentos_legado.buscar_nota_fiscal_boleto(url)


def analitico(page, cnpj: str, senha: str) -> list[dict]:
    url = _login(page, "webNewTrocaArquivo", [("input[name=pCpf]", cnpj), ("input[name=pSenha]", senha)])
    if "webNewTrocaArquivo.Menu" not in url:
        m = re.search(r'(webNewTrocaArquivo\.Menu\?[^"\']+)', page.content())
        if not m:
            raise ValueError("nao cheguei no menu da Troca de Arquivos")
        url = BASE_URL + m.group(1).replace("&amp;", "&")
    return documentos_legado.buscar_troca_arquivo_novo(url)


def lista_ativos(page, codigo: str, senha: str) -> bytes:
    page.goto(censo_legado.LOGIN_URL, timeout=30000)
    page.wait_for_load_state("networkidle")
    page.fill("input[name='pCodigoEmpresa']", codigo)
    page.keyboard.press("Tab")
    page.wait_for_selector("input[name='pSenha']", state="visible", timeout=10000)
    page.fill("input[name='pSenha']", senha)
    page.click("#Prosseguir")
    try:
        page.wait_for_url(re.compile("pIdSessao="), timeout=30000)
    except Exception:
        raise ValueError("Movimentacao: nao saiu da tela de login em 30s (reCAPTCHA recusou ou senha errada)")
    m_pessoa = re.search(r"pPessoa=(\d+)", page.content())
    sessao = {"pPessoa": m_pessoa.group(1) if m_pessoa else None,
              "pIdSessao": re.search(r"pIdSessao=([^&\"']+)", page.url).group(1)}
    registros = censo_legado_parser.parse_ativos_html(censo_legado.get_ativos(sessao), codigo)
    if not registros:
        raise ValueError("lista de ativos veio vazia")
    return censo_legado_parser.registros_para_csv_bytes(registros)


# ---------------------------------------------------------------- contrato

def _do_mes(venc: date | None, mes: int, ano: int) -> bool:
    return bool(venc) and venc.month == mes and venc.year == ano


def baixar_contrato(codigo: str, empresa: str, cred: dict, comp: str, mes: int, ano: int,
                    pasta: str, plano: str = "ODONTO") -> dict:
    """Baixa o que falta do contrato na pasta da competencia. Devolve
    {"gravados": [...], "avisos": [...], "status": [linhas do STATUS_FATURAS]}."""
    senha = cred["senha"]
    cnpj = re.sub(r"\D", "", cred.get("cnpj") or "").zfill(14)
    nomes = os.listdir(pasta) if os.path.isdir(pasta) else []
    falta = {n.upper() for n in pf.faltando(nomes, codigo, comp, plano, False)
             + pf.faltando_fontes(nomes, codigo, comp, plano, False)}
    precisa = lambda tipo, ext: pf.nome(tipo, plano, codigo, comp, ext).upper() in falta
    res = {"gravados": [], "avisos": [], "status": []}

    def etapa(nome, fn):
        ctx = browser.new_context(accept_downloads=True)
        try:
            return fn(ctx.new_page())
        except Exception as e:
            res["avisos"].append(f"{nome}: {str(e)[:200]}")
            log(f"{codigo} {nome}: ERRO {e}")
            return None
        finally:
            ctx.close()
            time.sleep(PAUSA)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            # 1. lista de boletos/NF (sempre: define o boleto da competencia e alimenta o status)
            notas = etapa("nota fiscal", lambda p: lista_notas(p, codigo, senha)) or []
            do_mes = [n for n in notas if _do_mes(n["vencimento"], mes, ano)]
            if notas and not do_mes:
                res["avisos"].append(f"Hapvida ainda não emitiu boleto com vencimento em {comp}")
            if len(do_mes) > 1:
                res["avisos"].append(f"{len(do_mes)} boletos com vencimento em {comp} (esperado 1 no Odonto) - nada gravado de boleto/NF")
            alvo = do_mes[0] if len(do_mes) == 1 else None

            # 2. boleto (Bemobi) - sempre que houver lista, pelo status
            abertos = etapa("boleto", lambda p: boletos_em_aberto(p, codigo, senha)) if notas else None
            if abertos is not None:
                if len(abertos) > 1:
                    res["avisos"].append(f"{len(abertos)} faturas em aberto no Bemobi (há vencida)")
                if alvo and precisa("BOLETO", "pdf"):
                    pdf = abertos.get(alvo["boleto_numero"])
                    if pdf:
                        _gravar(pasta, pf.nome("BOLETO", plano, codigo, comp, "pdf"), pdf)
                        res["gravados"].append("BOLETO")
                    else:
                        res["avisos"].append(f"boleto {alvo['boleto_numero']} de {comp} não está em aberto no Bemobi (já pago?)")
                hoje = date.today().strftime("%d/%m/%Y")
                recentes = sorted({n["vencimento"] for n in notas if n["vencimento"]})[-2:]
                for n in notas:
                    if n["vencimento"] not in recentes:
                        continue
                    st = "Em Aberto" if n["boleto_numero"] in abertos else "Liquidado"
                    base = {"Codigo": codigo, "Empresa": empresa, "Vencimento": n["vencimento"].strftime("%d/%m/%y"),
                            "Valor": f"{n['valor']:.2f}".replace(".", ","), "Status": st, "DataConsulta": hoje}
                    res["status"].append({**base, "TipoDocumento": "BOLETO", "NumeroDocumento": n["boleto_numero"]})
                    if n.get("nfe_pnu"):
                        res["status"].append({**base, "TipoDocumento": "NFE", "NumeroDocumento": n["nfe_pnu"]})

            # 3. NF (DANFSe Fortaleza, consulta publica)
            if alvo and precisa("NFE", "pdf"):
                if alvo.get("nfe_pnu") and alvo.get("nfe_codigo_verificacao"):
                    try:
                        pdf = documentos_legado.buscar_danfse_fortaleza(
                            CNPJ_PRESTADOR_FORTALEZA, alvo["nfe_pnu"], alvo["nfe_codigo_verificacao"])
                        _gravar(pasta, pf.nome("NFE", plano, codigo, comp, "pdf"), pdf)
                        res["gravados"].append("NFE")
                    except Exception as e:
                        res["avisos"].append(f"NF {alvo['nfe_pnu']}: DANFSe não veio de Fortaleza ({str(e)[:120]}) - prestador de outro município?")
                else:
                    res["avisos"].append(f"NF de {comp} ainda não emitida pela Hapvida")

            # 4. relatorio analitico (Troca de Arquivos)
            if precisa("RELATORIO_ANALITICO", "csv") or precisa("RELATORIO_ANALITICO", "pdf"):
                arqs = etapa("analítico", lambda p: analitico(p, cnpj, senha)) or []
                por_ext = {a["extensao"].lower().lstrip("."): a["conteudo"] for a in arqs}
                csv_b = por_ext.get("csv")
                if csv_b:
                    m = re.search(rb"com vencimento em (\d{2})/(\d{2})/(\d{4})", csv_b)
                    if not m or (int(m.group(2)), int(m.group(3))) != (mes, ano):
                        venc = m and m.group(0).decode()[-10:]
                        res["avisos"].append(f"analítico disponível é de outro mês (vencimento {venc or '?'}) - nada gravado")
                    else:
                        for ext in ("csv", "pdf"):
                            if por_ext.get(ext) and precisa("RELATORIO_ANALITICO", ext):
                                _gravar(pasta, pf.nome("RELATORIO_ANALITICO", plano, codigo, comp, ext), por_ext[ext])
                                res["gravados"].append(f"RELATORIO_ANALITICO.{ext}")
                elif arqs is not None and not arqs:
                    res["avisos"].append("Troca de Arquivos sem remessa disponível")

            # 5. lista de ativos
            if precisa("LISTA_ATIVOS", "csv"):
                csv_b = etapa("lista de ativos", lambda p: lista_ativos(p, codigo, senha))
                if csv_b:
                    _gravar(pasta, pf.nome("LISTA_ATIVOS", plano, codigo, comp, "csv"), csv_b)
                    res["gravados"].append("LISTA_ATIVOS.csv")
        finally:
            browser.close()
    log(f"{codigo}: gravados {res['gravados'] or 'nada'}" + (f" | avisos: {' | '.join(res['avisos'])}" if res["avisos"] else ""))
    return res


def gravar_status(base: str, comp: str, linhas: list[dict]) -> str | None:
    """_status/STATUS_FATURAS_CODE_{comp}.csv, mesmo formato do CSV do Cowork
    (lido pelo 161_importar_status_faturas_legado e pela auditoria). Mantem as
    linhas de contratos que nao foram consultados nesta rodada."""
    if not linhas:
        return None
    caminho = os.path.join(base, "_status", f"STATUS_FATURAS_CODE_{comp}.csv")
    novos = {l["Codigo"] for l in linhas}
    antigas = []
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8-sig") as f:
            antigas = [l for l in csv.DictReader(f, delimiter=";") if l["Codigo"] not in novos]
    buf = io.StringIO()
    w = csv.DictWriter(buf, COLUNAS_STATUS, delimiter=";", lineterminator="\n")
    w.writeheader()
    w.writerows(sorted(antigas + linhas, key=lambda l: (l["Codigo"], l["Vencimento"][-2:] + l["Vencimento"][3:5], l["TipoDocumento"])))
    _gravar(os.path.dirname(caminho), os.path.basename(caminho), ("﻿" + buf.getvalue()).encode("utf-8"))
    return caminho
