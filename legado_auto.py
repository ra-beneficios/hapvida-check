"""
Faturamento do Legado (portal antigo webhap) com login automatico - assume o
que era do Cowork (Andre, 07/10/2026: "libero o login automatico").

Credencial: cadastrada pelo Andre no Bitwarden (1 item por contrato, com as
URLs dos 4 portais) e copiada pro cofre de maquina pelo
sync_bitwarden_legado.py (host) como segredo `hapvida-legado`:
{CODIGO: {"senha": ..., "cnpj": ...}}. Contrato sem credencial aparece na
auditoria do faturamento_mensal como pendencia de cadastro (09/10/2026: sem Cowork).

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
  telas Download (Novo) e (Anterior). Lista os arquivos de TODOS os contratos
  do CNPJ - filtra pelo codigo. Grava a remessa cujo vencimento e' da
  competencia (mensal: EMPRESA_{COD}_REMESSA_n; copart: EMPRESA_FM_PSICO_...).
- webnewcadastrousuario (codigo + senha): tem reCAPTCHA v3 invisivel, que
  aprova o navegador normal - so' precisa esperar a URL com pIdSessao.
  Lista de ativos -> CSV (censo_legado_parser).

Odonto Premium e Saude (08/10/2026). Saude do Legado (Z8TUR, L&H ERQ43/ICBJY)
tem 2 boletos (mensalidade + copart): o de copart e' o da coluna FATURA do CSV
de coparticipacao da Troca de Arquivos.

Grava com os nomes do padrao NOV26 (padrao_faturamento) e so' o que falta na
pasta. XLSX/PDF derivados dos CSVs saem do gerar_xlsx_relatorios de sempre.
"""
import csv
import html
import io
import os
import re
import time
from datetime import date, datetime

import httpx
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
    """Nosso Numero -> PDF de cada fatura em aberto no Bemobi. O widget e' lento
    e as vezes nao responde (08/10: Gerenciar/download estourando 15-20s em lote) -
    2 tentativas com login novo."""
    for tentativa in range(2):
        try:
            return _boletos_em_aberto(page, codigo, senha)
        except Exception:
            if tentativa:
                raise
            time.sleep(PAUSA)


def _boletos_em_aberto(page, codigo: str, senha: str) -> dict[str, bytes]:
    _login(page, "webNewBoletoEmpresa", [("input[name=pCodigo]", codigo), ("input[name=pSenha]", senha)])
    try:
        page.wait_for_selector("iframe[src*='bemobi'], iframe[src*='izzie']", timeout=20000)
    except Exception:
        if "obrigacaoListaBemobi" in page.url and not page.locator("iframe").count():
            return {}  # sem fatura em aberto o portal nao embute o widget (checar_status_bemobi)
        raise ValueError("widget Bemobi nao carregou em 20s")
    frame = page.frame_locator("iframe[src*='bemobi'], iframe[src*='izzie']").first
    botoes = frame.locator('[aria-label="Baixar fatura em PDF"]')
    # Com fatura vencida o widget ja' abre na lista (IHWBW, 08/10) e o "Gerenciar" da tela e' o
    # de cartao de credito - so' clica em Gerenciar se a lista nao estiver aberta.
    nenhuma = frame.get_by_text("Nenhuma fatura encontrada")
    try:
        botoes.or_(nenhuma).first.wait_for(state="visible", timeout=8000)
    except Exception:
        frame.get_by_text("Gerenciar", exact=True).first.click(timeout=30000)
        botoes.or_(nenhuma).first.wait_for(state="visible", timeout=20000)
    if nenhuma.count() and not botoes.count():
        return {}  # 611X2 08/10: tudo pago - widget mostra "Nenhuma fatura encontrada"
    page.wait_for_timeout(1000)
    out = {}
    for i in range(botoes.count()):
        for tentativa in range(2):  # o download do widget as vezes nao dispara (IHWBW/SY8MI 08/10)
            try:
                with page.expect_download(timeout=30000) as d:
                    botoes.nth(i).click()
                break
            except Exception:
                if tentativa:
                    raise
                page.wait_for_timeout(3000)
        with open(d.value.path(), "rb") as f:
            pdf = f.read()
        m = re.search(r"N\.Num\.:\s*(\d{10,})", _texto_pdf(pdf))
        out[m.group(1) if m else f"sem_numero_{i}"] = pdf
        page.wait_for_timeout(1000)
    return out


def lista_notas(page, codigo: str, senha: str) -> list[dict]:
    """Linhas (boleto/vencimento/valor/NF) do contrato na tela de Nota Fiscal.
    - O login por codigo da L&H lista os boletos de TODOS os contratos do CNPJ
      (GRKKH: 9 contratos, varias paginas de 100) - pagina ate o fim e filtra.
    - A sessao vive no navegador (GET por fora volta vazio): navega pela page.
    - Em lote a pagina as vezes vem sem linhas (08/10): faz login de novo."""
    for tentativa in range(3):
        _login(page, "pk_nota_fiscal", [("input[name=pCodigoEmpresa]", codigo), ("input[name=pSenha]", senha)])
        todas, vistos = [], set()
        for _ in range(20):
            texto = page.content()
            novas = [l for l in documentos_legado.parse_nota_fiscal_html(texto)
                     if (l["contrato_codigo"], l["boleto_numero"]) not in vistos]
            vistos.update((l["contrato_codigo"], l["boleto_numero"]) for l in novas)
            todas += novas
            proxima = re.search(r'<a href="([^"]+)">Pr[óo]xima', texto)
            if not novas or not proxima:
                break
            page.goto(BASE_URL + html.unescape(proxima.group(1)), timeout=30000)
            page.wait_for_load_state("networkidle")
        minhas = [l for l in todas if l["contrato_codigo"].upper() == codigo.upper()]
        if minhas and any(l["vencimento"] for l in minhas):
            return minhas
        time.sleep(PAUSA)
    return []


_RE_LINK_TROCA = re.compile(r'<a id="[^"]+" _azstorage="(\d)" _ext="([^"]+)"_arquivo="[^"]*" _filename="([^"]+)" href="([^"]+)"')


def arquivos_troca(page, cnpj: str, senha: str, codigo: str) -> dict[str, bytes]:
    """Nome -> conteudo dos CSV/PDF do contrato nas telas "Download (Novo)" e
    "Download (Anterior)" da Troca de Arquivos. O login e' por CNPJ e lista os
    arquivos de TODOS os contratos daquele CNPJ (Z8TUR traz os do FRR8D; a L&H
    tem 5 contratos num CNPJ so') - por isso filtra pelo codigo no nome.
    Analitico mensal: EMPRESA_{COD}_REMESSA_{n}.CSV/.PDF. Coparticipacao (so'
    na Anterior): EMPRESA_FM_PSICO_{COD}_REMESSA_{n}.CSV e
    EMPRESA_FM_{COD}_REMESSA_{n}_PSICO.PDF (a Novo so' tem o ZIP, _azstorage=1)."""
    url = _login(page, "webNewTrocaArquivo", [("input[name=pCpf]", cnpj), ("input[name=pSenha]", senha)])
    if "webNewTrocaArquivo.Menu" not in url:
        m = re.search(r'(webNewTrocaArquivo\.Menu\?[^"\']+)', page.content())
        if not m:
            raise ValueError("nao cheguei no menu da Troca de Arquivos")
        url = BASE_URL + m.group(1).replace("&amp;", "&")
    cli = httpx.Client(timeout=60, follow_redirects=True)
    menu = cli.get(url).text
    out: dict[str, bytes] = {}
    for padrao in (r'href="(webNewTrocaArquivo2\.Download\?[^"]+)"', r'href="(webNewTrocaArquivo\.Download\?[^"]+)"'):
        m = re.search(padrao, menu)
        if not m:
            continue
        pagina = cli.get(BASE_URL + html.unescape(m.group(1))).text
        for azstorage, ext, nome, href in _RE_LINK_TROCA.findall(pagina):
            if azstorage != "0" or ext.upper() not in ("CSV", "PDF") or f"_{codigo}_" not in nome.upper() or nome in out:
                continue
            r = cli.get(BASE_URL + html.unescape(href))
            r.raise_for_status()
            out[nome] = r.content
    return out


def _remessa(nome: str) -> int:
    m = re.search(r"REMESSA_(\d+)", nome.upper())
    return int(m.group(1)) if m else 0


def _venc_csv(conteudo: bytes) -> tuple[int, int] | None:
    """(mes, ano) do vencimento: cabecalho "com vencimento em dd/mm/aaaa" (mensal)
    ou coluna DT VENCIMENTO da primeira linha (coparticipacao)."""
    m = re.search(rb"com vencimento em \d{2}/(\d{2})/(\d{4})", conteudo) or \
        re.search(rb'^"\d+";\s*[\d.]+;\s*\d{2}/(\d{2})/(\d{4});', conteudo, re.M)
    return (int(m.group(1)), int(m.group(2))) if m else None


def escolher_relatorios(arqs: dict[str, bytes], codigo: str, mes: int, ano: int) -> dict:
    """Da Troca de Arquivos, a remessa da competencia: {"mensal": {"csv","pdf"},
    "copart": {"csv","pdf","boletos": {nossos numeros}}, "outros_meses": [...]}."""
    res = {"mensal": {}, "copart": {}, "outros_meses": []}
    cod = codigo.upper()
    for nome in sorted(arqs, key=_remessa, reverse=True):
        up = nome.upper()
        if not up.endswith(".CSV"):
            continue
        tipo = "copart" if up.startswith(f"EMPRESA_FM_PSICO_{cod}_") else "mensal" if up.startswith(f"EMPRESA_{cod}_") else None
        if not tipo or res[tipo]:
            continue
        if _venc_csv(arqs[nome]) != (mes, ano):
            res["outros_meses"].append(nome)
            continue
        n = _remessa(nome)
        pdf = (f"EMPRESA_FM_{cod}_REMESSA_{n}_PSICO.PDF" if tipo == "copart" else f"EMPRESA_{cod}_REMESSA_{n}.PDF")
        res[tipo] = {"csv": arqs[nome], "pdf": next((v for k, v in arqs.items() if k.upper() == pdf), None)}
        if tipo == "copart":
            res[tipo]["boletos"] = set(re.findall(rb'^"(\d{10,})";', arqs[nome], re.M))
            res[tipo]["boletos"] = {b.decode() for b in res[tipo]["boletos"]}
    return res


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
    m_pessoa = None
    for _ in range(10):  # 08/10 (BY3S8): pagina ainda carregando -> pPessoa=None -> 302
        m_pessoa = re.search(r"pPessoa=(\d+)", page.content())
        if m_pessoa:
            break
        page.wait_for_timeout(1000)
    if not m_pessoa:
        raise ValueError("Movimentacao: logou mas nao achei pPessoa na pagina")
    sessao = {"pPessoa": m_pessoa.group(1),
              "pIdSessao": re.search(r"pIdSessao=([^&\"']+)", page.url).group(1)}
    registros = censo_legado_parser.parse_ativos_html(censo_legado.get_ativos(sessao), codigo)
    if not registros:
        raise ValueError("lista de ativos veio vazia")
    return censo_legado_parser.registros_para_csv_bytes(registros)


# ---------------------------------------------------------------- contrato

def _do_mes(venc: date | None, mes: int, ano: int) -> bool:
    return bool(venc) and venc.month == mes and venc.year == ano


_RE_VENC_BOLETO = re.compile(r"Vencimento:\s*(\d{2}/\d{2}/\d{4})")


def baixar_contrato(codigo: str, empresa: str, cred: dict, comp: str, mes: int, ano: int,
                    pasta: str, plano: str = "ODONTO") -> dict:
    """Baixa o que falta do contrato na pasta da competencia. Devolve
    {"gravados": [...], "avisos": [...], "status": [linhas do STATUS_FATURAS], "copart": bool}.

    Saude do Legado (Z8TUR, L&H ERQ43/ICBJY): 2 boletos no mes. O de
    coparticipacao e' o que aparece na coluna FATURA do CSV de copart da Troca
    de Arquivos; o outro e' a mensalidade. Sem o CSV de copart nao da' pra
    separar - nada de boleto/NF e' gravado (aviso)."""
    senha = cred["senha"]
    cnpj = re.sub(r"\D", "", cred.get("cnpj") or "").zfill(14)
    res = {"gravados": [], "avisos": [], "status": [], "copart": False}

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
            # 1. lista de boletos/NF + Troca de Arquivos (define o que e' do mes e o que e' copart)
            notas = etapa("nota fiscal", lambda p: lista_notas(p, codigo, senha))
            if notas == []:
                res["avisos"].append("lista de notas fiscais veio vazia (login ok, nenhuma linha lida) - boleto/NF/status não verificados")
            notas = notas or []
            do_mes = [n for n in notas if _do_mes(n["vencimento"], mes, ano)]
            if notas and not do_mes:
                vistos = sorted({f"{n['vencimento']:%m/%Y}" for n in notas if n["vencimento"]})
                res["avisos"].append(f"nenhum boleto com vencimento em {comp} na lista de notas"
                                     f" (lida: {len(notas)} linhas, vencimentos {', '.join(vistos[-3:]) or 'ilegíveis'})")
            arqs = etapa("Troca de Arquivos", lambda p: arquivos_troca(p, cnpj, cred.get("senha_troca") or senha, codigo))
            rel = escolher_relatorios(arqs or {}, codigo, mes, ano)
            copart_boletos = rel["copart"].get("boletos", set())

            # plano de cada boleto do mes
            boleto_plano: dict[str, str] = {}
            for n in do_mes:
                boleto_plano[n["boleto_numero"]] = "COPARTICIPACAO" if n["boleto_numero"] in copart_boletos else plano
            contagem = {p: list(boleto_plano.values()).count(p) for p in set(boleto_plano.values())}
            if any(v > 1 for v in contagem.values()):
                res["avisos"].append(f"boletos de {comp} não separáveis por tipo ({len(do_mes)} boletos"
                                     + ("; CSV de coparticipação do mês não está na Troca de Arquivos" if plano == "SAUDE" and not copart_boletos else "")
                                     + ") - nada gravado de boleto/NF")
                boleto_plano = {}
            res["copart"] = "COPARTICIPACAO" in boleto_plano.values() or bool(rel["copart"])
            if rel["copart"] and plano != "SAUDE":
                res["avisos"].append("CSV de coparticipação encontrado num contrato Odonto - ignorado")
                res["copart"] = False

            nomes = os.listdir(pasta) if os.path.isdir(pasta) else []
            falta = {n.upper() for n in pf.faltando(nomes, codigo, comp, plano, res["copart"])
                     + pf.faltando_fontes(nomes, codigo, comp, plano, res["copart"])}
            precisa = lambda tipo, p, ext: pf.nome(tipo, p, codigo, comp, ext).upper() in falta

            # 2. boletos (Bemobi) - sempre que houver lista, pelo status
            abertos = etapa("boleto", lambda p: boletos_em_aberto(p, codigo, senha)) if notas else None
            if abertos:
                # 26/10/2026: boleto do mes sem NF emitida ainda nao aparece na tela de Nota Fiscal, so' no
                # Bemobi (2M2DX/Y4VLX NOV26, venc. 10/11). Completa pelo vencimento impresso no boleto.
                extras = {}
                for numero, pdf in abertos.items():
                    m = None if numero in boleto_plano else _RE_VENC_BOLETO.search(_texto_pdf(pdf))
                    if m and _do_mes(datetime.strptime(m.group(1), "%d/%m/%Y").date(), mes, ano):
                        extras[numero] = "COPARTICIPACAO" if numero in copart_boletos else plano
                planos = list(boleto_plano.values()) + list(extras.values())
                if extras and any(planos.count(p) > 1 for p in set(planos)):
                    res["avisos"].append(f"boletos de {comp} no Bemobi não separáveis por tipo ({', '.join(extras)}) - nada gravado")
                elif extras:
                    boleto_plano.update(extras)
                    res["avisos"] = [a for a in res["avisos"] if not a.startswith("nenhum boleto com vencimento")]
                    res["avisos"].append(f"boleto(s) de {comp} só no Bemobi, NF ainda não emitida: {', '.join(extras)}")
            if abertos is not None:
                vencidos = [n for n in notas if n["boleto_numero"] in abertos and n["vencimento"]
                            and (n["vencimento"].year, n["vencimento"].month) < (ano, mes)]
                if vencidos:
                    res["avisos"].append("fatura vencida em aberto no Bemobi: " + ", ".join(
                        f"{n['vencimento']:%m/%Y} R$ {n['valor']:.2f}" for n in vencidos))
                for numero, p in boleto_plano.items():
                    if not precisa("BOLETO", p, "pdf"):
                        continue
                    pdf = abertos.get(numero)
                    if pdf:
                        _gravar(pasta, pf.nome("BOLETO", p, codigo, comp, "pdf"), pdf)
                        res["gravados"].append(f"BOLETO_{p}")
                    else:
                        res["avisos"].append(f"boleto {numero} ({p}) de {comp} não está em aberto no Bemobi (já pago?)")
                res["status"] = linhas_status(codigo, empresa, notas, abertos)

            # 3. NF de cada boleto do mes (DANFSe Fortaleza, consulta publica)
            for n in do_mes:
                p = boleto_plano.get(n["boleto_numero"])
                if not p or not precisa("NFE", p, "pdf"):
                    continue
                if n.get("nfe_pnu") and n.get("nfe_codigo_verificacao"):
                    try:
                        pdf = documentos_legado.buscar_danfse_fortaleza(
                            CNPJ_PRESTADOR_FORTALEZA, n["nfe_pnu"], n["nfe_codigo_verificacao"])
                        _gravar(pasta, pf.nome("NFE", p, codigo, comp, "pdf"), pdf)
                        res["gravados"].append(f"NFE_{p}")
                    except Exception as e:
                        res["avisos"].append(f"NF {n['nfe_pnu']}: DANFSe não veio de Fortaleza ({str(e)[:120]}) - prestador de outro município?")
                else:
                    res["avisos"].append(f"NF do boleto {n['boleto_numero']} ({p}) ainda não emitida pela Hapvida")

            # 4. relatorios analiticos da competencia (mensal + copart)
            if arqs is not None:
                for tipo, p in (("mensal", plano), ("copart", "COPARTICIPACAO")):
                    if tipo == "copart" and not res["copart"]:
                        continue
                    r = rel[tipo]
                    if not r:
                        if precisa("RELATORIO_ANALITICO", p, "csv"):
                            outros = [x for x in rel["outros_meses"] if ("_FM_PSICO_" in x.upper()) == (tipo == "copart")]
                            res["avisos"].append(f"analítico {p} de {comp} não está na Troca de Arquivos"
                                                 + (f" (só de outro mês: {', '.join(outros[:2])})" if outros else ""))
                        continue
                    for ext in ("csv", "pdf"):
                        if r.get(ext) and precisa("RELATORIO_ANALITICO", p, ext):
                            _gravar(pasta, pf.nome("RELATORIO_ANALITICO", p, codigo, comp, ext), r[ext])
                            res["gravados"].append(f"RELATORIO_ANALITICO_{p}.{ext}")
                    if not r.get("pdf"):
                        res["avisos"].append(f"analítico {p}: CSV sem o PDF da mesma remessa")

            # 5. lista de ativos
            if precisa("LISTA_ATIVOS", plano, "csv"):
                csv_b = etapa("lista de ativos", lambda p: lista_ativos(p, codigo, senha))
                if csv_b:
                    _gravar(pasta, pf.nome("LISTA_ATIVOS", plano, codigo, comp, "csv"), csv_b)
                    res["gravados"].append("LISTA_ATIVOS.csv")
        finally:
            browser.close()
    log(f"{codigo}: gravados {res['gravados'] or 'nada'}" + (f" | avisos: {' | '.join(res['avisos'])}" if res["avisos"] else ""))
    return res


def linhas_status(codigo: str, empresa: str, notas: list[dict], abertos: dict) -> list[dict]:
    """Boleto/NF dos 2 vencimentos mais recentes (mesmo recorte do CSV do Cowork):
    em aberto no Bemobi = "Em Aberto", senao "Liquidado". Valor = o da fatura
    (o Bemobi mostra com juros de mora)."""
    hoje = date.today().strftime("%d/%m/%Y")
    recentes = sorted({n["vencimento"] for n in notas if n["vencimento"]})[-2:]
    out = []
    for n in notas:
        if n["vencimento"] not in recentes:
            continue
        base = {"Codigo": codigo, "Empresa": empresa, "Vencimento": n["vencimento"].strftime("%d/%m/%y"),
                "Valor": f"{n['valor']:.2f}".replace(".", ","),
                "Status": "Em Aberto" if n["boleto_numero"] in abertos else "Liquidado", "DataConsulta": hoje}
        out.append({**base, "TipoDocumento": "BOLETO", "NumeroDocumento": n["boleto_numero"]})
        if n.get("nfe_pnu"):
            out.append({**base, "TipoDocumento": "NFE", "NumeroDocumento": n["nfe_pnu"]})
    return out


def consultar_status(codigo: str, empresa: str, cred: dict) -> tuple[list[dict], list[str]]:
    """So' o status (2 logins: notas + Bemobi), pra checagem diaria."""
    avisos = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            ctx = browser.new_context(accept_downloads=True)
            notas = lista_notas(ctx.new_page(), codigo, cred["senha"])
            ctx.close()
            if not notas:
                return [], ["lista de notas fiscais veio vazia - status não atualizado"]
            time.sleep(PAUSA)
            ctx = browser.new_context(accept_downloads=True)
            abertos = boletos_em_aberto(ctx.new_page(), codigo, cred["senha"])
            ctx.close()
        finally:
            browser.close()
    linhas = linhas_status(codigo, empresa, notas, abertos)
    if not linhas:
        avisos.append(f"{len(notas)} notas lidas mas nenhuma com vencimento legível - status não atualizado")
    return linhas, avisos


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


def main_status() -> None:
    """Checagem diaria de status (cron, antes do 161_importar_status_faturas_legado):
    substitui a checagem periodica que o Cowork fazia, pros contratos assumidos."""
    import faturamento_mensal as fm
    import secrets_store
    comp = fm.competencia_padrao(date.today())
    legado = fm.contratos_legado()
    assumidos, creds = fm.legado_assumido(legado)
    linhas, falhas = [], []
    for codigo in sorted(assumidos):
        try:
            l, av = consultar_status(codigo, legado[codigo], creds[codigo])
            linhas += l
            if av:
                falhas.append(f"{codigo}: {'; '.join(av)}")
            abertos = sorted({x["Vencimento"] for x in l if x["Status"] == "Em Aberto" and x["TipoDocumento"] == "BOLETO"})
            log(f"status {codigo}: {len(l)} linhas, em aberto: {', '.join(abertos) or 'nada'}")
        except Exception as e:
            falhas.append(f"{codigo}: {str(e)[:150]}")
            log(f"status {codigo}: ERRO {e}")
        time.sleep(PAUSA)
    caminho = gravar_status(fm.BASE, comp, linhas)
    log(f"status gravado em {caminho} | falhas: {falhas or 'nenhuma'}")


def main_vidas(destino: str) -> None:
    """Lista de ativos de cada contrato assumido, so' pra atualizar a base de vidas
    (vidas/atualizar_hapvida.py, seg/qua/sex). Grava em `destino` (fora da pasta do
    cliente: a lista que vai no faturamento continua sendo a do faturamento_mensal)."""
    import faturamento_mensal as fm
    legado = fm.contratos_legado()
    assumidos, creds = fm.legado_assumido(legado)
    os.makedirs(destino, exist_ok=True)
    falhas = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            for codigo in sorted(assumidos):
                ctx = browser.new_context()
                try:
                    inicio = datetime.now()
                    csv_b = lista_ativos(ctx.new_page(), codigo, creds[codigo]["senha"])
                    _gravar(destino, f"LEGADO_{codigo}_{inicio:%Y%m%d_%H%M}.csv", csv_b)
                    log(f"vidas {codigo}: lista gravada")
                except Exception as e:
                    falhas.append(f"{codigo}: {str(e)[:150]}")
                    log(f"vidas {codigo}: ERRO {e}")
                finally:
                    ctx.close()
                time.sleep(PAUSA)
        finally:
            browser.close()
    log(f"vidas: {len(assumidos)} contratos | falhas: {falhas or 'nenhuma'}")


if __name__ == "__main__":
    import sys
    if "--status" in sys.argv:
        main_status()
    elif "--vidas" in sys.argv:
        main_vidas(sys.argv[sys.argv.index("--vidas") + 1])
    else:
        sys.exit("uso: legado_auto.py --status | --vidas DESTINO   (download mensal roda pelo faturamento_mensal.py)")
