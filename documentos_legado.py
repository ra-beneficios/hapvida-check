"""
Nota Fiscal/Boleto e Coparticipacao do Grupo Legado, via sessao manual
(login bloqueado por captcha, #77 - Andre loga e passa a URL autenticada,
nunca automatizamos o login em si).

ATUALIZADO 07/10/2026: Andre LIBEROU o login automatico no Legado. O fluxo
mensal agora e' o legado_auto.py (login com credencial do Bitwarden nos 4
portais, reaproveitando as funcoes daqui). A regra "nunca logar" abaixo vale
so' pro historico - as funcoes com URL de sessao manual continuam servindo
pro endpoint /processar-sessao-legado.

Mapeado ao vivo em 01/08 com 2 contratos reais (S7HNK - Odonto Premium,
Z8TUR - Saude), a partir do hub publico www2.hapvida.com.br/empresas.
Achados-chave (ver Addendum 10):
- Nota Fiscal e Boleto sao a MESMA fonte aqui (diferente da API moderna,
  que tem endpoints separados) - a tela "pk_nota_fiscal.listaNotasFiscais"
  ja traz BOLETO+VENCIMENTO+VALOR por linha, com um link por linha que leva
  aos dados de verificacao da NFe (numero NFS-e + codigo de verificacao) -
  NAO um link direto de impressao como na API moderna (isso muda por
  municipio do prestador, ex: Fortaleza usa iss.fortaleza.ce.gov.br sem
  parametro de consulta direta conhecido ainda).
- Coparticipacao (`webNewCadastroUsuario.previaCoparticipacao`, dentro do
  mesmo Sistema de Movimentacao) so existe pra contratos SAUDE - testado
  ausente no menu de um contrato Odonto Premium (S7HNK), presente com dado
  real no Saude (Z8TUR). Nao e limitacao de acesso, e a Hapvida nao expor
  esse recurso pra Odonto.
- Boleto "Bemobi" (`webNewBoletoEmpresa.obrigacaoListaBemobi`) e "Custo
  Operacional" testados vazios em ambos os contratos - nao sao a fonte
  real de boleto (que ja vem pela Nota Fiscal).

Achados 04/08 (Z8TUR/FRR8D):
- O boleto "Bemobi" NAO esta vazio de verdade - carrega via iframe JS
  (invoicescheckout.bemobi.com, sistema white-label de pagamento) que nao
  aparece num GET simples. Descoberto por acidente: o PDF final de cada
  boleto/coparticipacao fica hospedado em invoices.izzie.io com URL
  assinada (CloudFront-style, expira em ~5min). Tentei achar a API por
  tras (api.izzie.io/v1/invoices existe, autentica com Bearer, mas o
  token do cookie so tem escopo "izzie-tokenize" pra pagamento com
  cartao - 403 Forbidden pra listar faturas). NAO fui alem disso -
  escalar permissao de token de sistema de pagamento de terceiro nao e
  "mapear", e sondar sem autorizacao. Pra pegar o PDF do boleto hoje:
  Andre clica 1x no iframe da tela "2a via de Empresa - Faturas" e passa
  a URL do izzie.io que abrir - so isso, sem repetir login nem captcha.
- NFe/DANFSe: RESOLVIDO de ponta a ponta, sem captcha, 100% automatizavel
  (`buscar_danfse_fortaleza` abaixo). A pagina de consulta publica de
  Fortaleza (iss.fortaleza.ce.gov.br) - achada a partir do texto
  "Emissao NFS-e" que a propria tela de Nota Fiscal do webhap mostra -
  tem um link "Validar NFS-e (por Numero/RPS)" sem login nem captcha
  (o unico captcha e no portal NACIONAL nfse.gov.br, que nao precisamos
  usar). O formulario pede CNPJ do prestador + valor + numero NFS-e +
  codigo de verificacao - os 4 ja vem da propria tela de Nota Fiscal
  (`buscar_nota_fiscal_boleto` abaixo). A pagina de resultado embute a
  DANFSe direto num `<object data="...">` (recurso RichFaces preso a
  sessao - precisa ser a MESMA sessao/cookies do form, nao da pra
  reusar a URL do resource isolada). Testado com Z8TUR e FRR8D, os dois
  prestadores sao a mesma Hapvida (CNPJ 63.554.067/0001-98, Fortaleza).
  Se outro municipio aparecer nos proximos contratos, o link muda (o
  texto "Emissao NFS-e" sempre informa qual).

Achado 17/08 (DAVJV): confirmado que o municipio realmente muda por
prestador - DAVJV (UTC Oleo e Gas) tem prestador em Sao Paulo (CNPJ
44.649.812/0001-38), nao Fortaleza. Portal publico equivalente:
nfe.prefeitura.sp.gov.br. Fluxo parecido mas com 2 passos, nao 1:
1. GET `contribuinte/notaprint.aspx?nf={numero_nfse}&inscricao={pCd
   Empresa/inscricao do prestador}&verificacao={codigo_verificacao}` -
   pagina "Aguarde... Carregando Nota Fiscal..." (so' seta cookie de
   sessao, nao tem o PDF ainda).
2. GET `contribuinte/notaprintpdf.aspx?` com os MESMOS 3 parametros,
   MESMA sessao/cookies do passo 1 - devolve o PDF direto (`content-type:
   application/pdf`, sem precisar parsear HTML/iframe). Testado, PDF
   valido, sem login nem captcha, batendo com o valor real (R$362.222,00).
   Nao implementado como funcao ainda (`buscar_danfse_fortaleza` so cobre
   Fortaleza) - se aparecer de novo, vale generalizar por municipio em vez
   de copiar a funcao.

Achado 17/08 (DAVJV, fonte alternativa pra fatura): a tela de "Nota
Fiscal" (`pk_nota_fiscal.listaNotasFiscais`) as vezes devolve erro real do
Oracle (`ORA-01830: a imagem do formato da data termina antes de
converter a string de entrada inteira`) em vez de sessao expirada ou
lista vazia - visto no dominio alternativo sigo.sh.srv.br/pls/webndi
(mesma Hapvida, marca "NDI"/Notre Dame Intermedica, sistema espelhado).
Quando isso acontecer, a tela "Troca de Arquivos (Novo)"
(`webNewTrocaArquivo.Menu` -> link "BAIXAR ARQUIVOS - DOWNLOAD (Novo)" ->
`webNewTrocaArquivo2.Download` -> arquivos `_azstorage="0"` via
`webNewTrocaArquivo2.DownloadViaLobstore`) e uma fonte alternativa: o CSV
`EMPRESA_<codigo>_REMESSA_<n>.CSV` tem um cabecalho de 6 linhas com
obrigacao/vencimento/valor (exatamente o que `faturas_legado` precisa),
seguido de milhares de linhas por beneficiario (CPF, nome, nome da mae -
NUNCA persistir isso, so o cabecalho). `buscar_troca_arquivo_novo` (funcao
abaixo) ja existe mas tem a URL de download final hardcoded pra
"/pls/webhap/" - quebra nesse dominio alternativo "/pls/webndi/"; usado
manualmente com curl desta vez, nao corrigido na funcao ainda.

Achado real 31/08/2026 (2M2DX - HC2 Odonto Premium), fecha o que o Addendum
de 04/08 tinha deixado manual: o boleto (Bemobi/izzie.io) SO precisa da URL
"webNewBoletoEmpresa.obrigacaoListaBemobi" que o Andre ja passa - o resto e
automatizavel com Playwright, sem escalar token nem chamar api.izzie.io
direto (o 403 de antes era isso, uma tentativa de bypassar a UI - errado).
O fluxo real, clicando exatamente como um humano clicaria: dentro do
<iframe> (cross-origin, precisa de `page.frame_locator`), clicar em
"Gerenciar" revela um botao SEM TEXTO mas com aria-label="Baixar fatura em
PDF" - clicar nele dispara um download de verdade (`page.expect_download`
pega o arquivo, nao precisa mais interceptar rede). A sessao dessa URL
Bemobi expira RAPIDO (poucos minutos, testado): fazer tudo numa unica
chamada Playwright, sem pausar pra tirar screenshot no meio ou a sessao
morre ("Nao foi possivel autenticar... Sessao nao disponivel pra essa
empresa"). Ver `baixar_boleto_bemobi` abaixo.

Uso: Andre passa a URL de "Nota Fiscal" e/ou "Coparticipacao" e/ou "2a via
de Empresa - Faturas" (Bemobi) ja autenticada (sessao manual). Estas
funcoes so fazem GET/clique direto nessas URLs e gravam o que vier - nunca
tentam logar sozinhas. `buscar_danfse_fortaleza` e diferente: ela PRECISA
logar/navegar sozinha, mas e um portal PUBLICO da prefeitura (consulta sem
senha), nao o sistema da Hapvida - nao viola a regra de nunca automatizar
login do Hapvida.
"""
import re
from datetime import datetime

import httpx
import psycopg2
from playwright.sync_api import sync_playwright


def _parse_data_br(data_str, formato="%d/%m/%y"):
    if not data_str:
        return None
    try:
        return datetime.strptime(data_str.strip(), formato).date()
    except ValueError:
        try:
            return datetime.strptime(data_str.strip(), "%d/%m/%Y").date()
        except ValueError:
            return None


def _num(valor_str):
    if not valor_str:
        return None
    v = valor_str.strip().replace(".", "").replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def _normalizar_cpf(val):
    if not val:
        return None
    s = re.sub(r"\D", "", val)
    return s.zfill(11) if s else None


def buscar_danfse_fortaleza(cnpj_prestador: str, numero_nfse: str, codigo_verificacao: str) -> bytes:
    """Baixa a DANFSe em PDF via consulta publica (sem senha, sem captcha)
    do portal da prefeitura de Fortaleza (iss.fortaleza.ce.gov.br) -
    achado 04/08, ver docstring do modulo. cnpj_prestador so digitos.
    Levanta ValueError se a nota nao for encontrada/o PDF nao vier."""
    c = httpx.Client(timeout=30, follow_redirects=True)
    r1 = c.get("https://iss.fortaleza.ce.gov.br")
    r2 = c.get(str(r1.url).rsplit("/", 1)[0] + "/home.seam")
    cid = str(r2.url).split("cid=")[-1]

    form_login = {
        "login": "login", "autoScroll": "", "login:j_idcl": "", "login:_link_hidden_": "",
        "javax.faces.ViewState": "j_id1", "login:j_id50": "login:j_id50",
    }
    c.post("https://iss.fortaleza.ce.gov.br/grpfor/login.seam", params={"cid": cid}, data=form_login)

    form_consulta = {
        "validarNotaForm": "validarNotaForm",
        "validarNotaForm:opConsulta": "0",  # 0 = Nota Fiscal Eletronica
        "validarNotaForm:numNfse": numero_nfse,
        "validarNotaForm:numCodVerificacao": codigo_verificacao,
        "validarNotaForm:opPrestadorNF": "1",  # 1 = CNPJ
        "validarNotaForm:nfseCnpjPrestador": cnpj_prestador,
        "validarNotaForm:j_id92": "Consultar",
        "javax.faces.ViewState": "j_id2",
    }
    r4 = c.post(
        "https://iss.fortaleza.ce.gov.br/grpfor/pagesPublic/validarNota.seam",
        params={"cid": cid}, data=form_consulta,
    )

    m = re.search(r'id="j_id32:pdfLink" data="([^"]+)"', r4.text)
    if not m:
        raise ValueError("Nota nao encontrada ou sem PDF de DANFSe na resposta")

    pdf_url = "https://iss.fortaleza.ce.gov.br" + m.group(1).replace("&amp;", "&")
    r5 = c.get(pdf_url)  # precisa da MESMA sessao (cookies) que gerou o link
    if r5.content[:4] != b"%PDF":
        raise ValueError(f"Resposta nao e PDF (status {r5.status_code})")
    return r5.content


def buscar_troca_arquivo_novo(url_menu: str) -> list[dict]:
    """Baixa o(s) arquivo(s) do analitico mais recente via 'Troca de Arquivos
    (Novo)' - achado real 13/08 (BY3S8): ao contrario do que o Addendum 10
    concluiu ("precisa engenharia reversa do JS"), o caso mais comum
    (_azstorage="0") NAO precisa de AJAX nenhum - o proprio href do link é
    uma URL de download completa e direta, funciona com GET simples. So o
    caso _azstorage="1" (nao visto ainda) precisaria do fluxo
    GetURLDownloadByReport com filtro base64 - não implementado aqui, vai
    levantar RuntimeError se aparecer, pra nao silenciar esse caso.

    url_menu: a URL de 'webNewTrocaArquivo.Menu?...' que o Andre passa.
    Retorna 1 dict por arquivo: {"nome_arquivo": str, "extensao": str,
    "conteudo": bytes}."""
    c = httpx.Client(timeout=30, follow_redirects=True)
    r_menu = c.get(url_menu)
    r_menu.raise_for_status()

    m = re.search(r'href="(webNewTrocaArquivo2\.Download\?[^"]+)"', r_menu.text)
    if not m:
        raise ValueError("Link 'BAIXAR ARQUIVOS - DOWNLOAD (Novo)' nao encontrado no menu")
    url_base = url_menu.rsplit("/", 1)[0] + "/" if "/" in url_menu.split("?")[0] else ""
    # a URL do menu e do tipo .../pls/webhap/webNewTrocaArquivo.Menu?... - troca só o nome do endpoint
    url_download = re.sub(r"webNewTrocaArquivo\.Menu", "webNewTrocaArquivo2.Download", url_menu.split("?")[0]) + "?" + m.group(1).split("?", 1)[1]
    r_download = c.get(url_download)
    r_download.raise_for_status()

    arquivos = []
    nomes_vistos = set()
    for am in re.finditer(
        r'<a id="[^"]+" _azstorage="(\d)" _ext="([^"]+)"_arquivo="[^"]*" _filename="([^"]+)" href="([^"]+)"',
        r_download.text,
    ):
        azstorage, ext, filename, href = am.groups()
        if filename in nomes_vistos:
            # achado real 13/08 (BY3S8): a pagina as vezes repete a MESMA linha (mesmo
            # _filename, _arquivo diferente) - mesmo conteudo, so baixa uma vez.
            continue
        nomes_vistos.add(filename)
        if azstorage != "0":
            raise RuntimeError(
                f"Arquivo '{filename}' usa _azstorage={azstorage} (fluxo GetURLDownloadByReport, "
                "nao implementado) - achado novo, precisa mapear esse caso antes de prosseguir"
            )
        url_arquivo = url_menu.split("/pls/webhap/")[0] + "/pls/webhap/" + href
        r_arquivo = c.get(url_arquivo)
        r_arquivo.raise_for_status()
        arquivos.append({"nome_arquivo": filename, "extensao": ext, "conteudo": r_arquivo.content})
    return arquivos


def buscar_nota_fiscal_boleto(url_sessao: str) -> list[dict]:
    """GET direto na URL de sessao ja autenticada de 'Nota Fiscal'
    (pk_nota_fiscal.listaNotasFiscais). Devolve 1 linha por boleto, com o
    numero da NFS-e e codigo de verificacao quando disponiveis (extraidos
    do link de cada linha, achado real no #S7HNK)."""
    r = httpx.get(url_sessao, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return parse_nota_fiscal_html(r.text)


def parse_nota_fiscal_html(texto: str) -> list[dict]:
    """Linhas da tela pk_nota_fiscal.listaNotasFiscais (separado em 08/10/2026
    pro legado_auto ler direto da pagina do navegador, sem 2o GET)."""
    linhas = []
    # Achado real 13/08 (achado no 611X2): a tabela tem pelo menos 2 formatos de
    # espacamento HTML entre as celulas - a versao antiga (regex rigida
    # </td>\s*<td>) nao casava com esse segundo formato (tudo numa linha so por
    # <tr>). Fix: parsear por bloco <tr>...</tr> e extrair todos os <a href>
    # daquele bloco, na ordem - agnostico a espacamento entre tags.
    for tr in re.finditer(r"<tr>(.*?)</tr>", texto, re.DOTALL):
        celulas = re.findall(r'<a[^>]*href="([^"]*)"[^>]*>\s*([^<]*?)\s*</a>', tr.group(1))
        if len(celulas) < 5:
            continue
        href = celulas[0][0]
        contrato, pessoa_razao, boleto, vencimento, valor = (c[1] for c in celulas[:5])
        nfe_pnu = None
        nfe_verificacao = None
        pnu_m = re.search(r"pnu_nfse=(\d+)", href)
        verif_m = re.search(r"pcd_verificacao=(\d+)", href)
        if pnu_m:
            nfe_pnu = pnu_m.group(1)
        if verif_m:
            nfe_verificacao = verif_m.group(1)
        linhas.append(
            {
                "contrato_codigo": contrato.strip(),
                "pessoa_razao": pessoa_razao.strip(),
                "boleto_numero": boleto.strip(),
                "vencimento": _parse_data_br(vencimento),
                "valor": _num(valor),
                "nfe_pnu": nfe_pnu,
                "nfe_codigo_verificacao": nfe_verificacao,
            }
        )
    return linhas


def baixar_boleto_bemobi(url_sessao: str, indice: int = -1) -> bytes:
    """GET direto na URL de sessao ja autenticada de '2a via de Empresa -
    Faturas' (webNewBoletoEmpresa.obrigacaoListaBemobi) e baixa o PDF real
    do boleto - achado real 31/08/2026 (2M2DX), ver docstring do modulo.
    Clica exatamente como um humano clicaria (Gerenciar -> botao com
    aria-label 'Baixar fatura em PDF' dentro do iframe cross-origin da
    Bemobi/izzie.io) - NAO tenta chamar a API da Bemobi direto (o token
    dessa sessao so tem escopo pra tokenizar cartao, chamar a API na mao
    da 403, ver Addendum de 04/08). A sessao Bemobi expira rapido (poucos
    minutos) - por isso tudo aqui roda numa unica passada, sem pausas.

    Achado real 31/08/2026 (SY8MI): quando ha fatura VENCIDA em aberto, a
    tela "Gerenciar" lista TODAS as faturas em aberto (nao so a atual) -
    cada uma com seu proprio botao 'Baixar fatura em PDF', entao pode ter
    mais de 1 match. `indice=-1` (padrao) pega a ULTIMA da lista, que nos
    testes ate agora e sempre a mais recente/competencia atual - mas isso
    tambem e um sinal real de inadimplencia que vale reportar (nao so
    baixar o PDF e seguir).

    Levanta RuntimeError se o download nao disparar (sessao expirada,
    layout mudou, etc)."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(accept_downloads=True)
        page = ctx.new_page()
        try:
            page.goto(url_sessao, timeout=20000)
            page.wait_for_load_state("networkidle", timeout=15000)
            frame = page.frame_locator("iframe")
            frame.locator("text=Gerenciar").click(timeout=10000)
            page.wait_for_timeout(1000)
            botoes = frame.locator('[aria-label="Baixar fatura em PDF"]')
            with page.expect_download(timeout=8000) as dl_info:
                botoes.nth(indice if indice >= 0 else botoes.count() + indice).click()
            download = dl_info.value
            path = download.path()
            if not path:
                raise RuntimeError("Download nao produziu arquivo (path vazio)")
            with open(path, "rb") as f:
                return f.read()
        except Exception as e:
            raise RuntimeError(f"Nao consegui baixar o boleto Bemobi: {e}") from e
        finally:
            browser.close()


def buscar_coparticipacao(url_sessao: str) -> list[dict]:
    """GET direto na URL de sessao ja autenticada de 'Relatorio de Previa
    de Coparticipacao'. So existe/traz dado pra contratos SAUDE. Devolve
    1 linha por procedimento."""
    r = httpx.get(url_sessao, timeout=30, follow_redirects=True)
    r.raise_for_status()

    linhas = []
    blocos = re.split(r"Dados Usuario", r.text)[1:]
    for bloco in blocos:
        contrato_m = re.search(r"<b>Contrato:\s*</b>\s*([A-Z0-9]+)", bloco)
        usuario_m = re.search(r"<b>Usuario:\s*</b>\s*([A-Z0-9]+)-([^&<]+)", bloco)
        cpf_m = re.findall(r"<b>CPF:\s*</b>\s*([\d.\-]*)", bloco)
        titular_m = re.search(r"<b>Titular:\s*</b>\s*([A-Z0-9]+)-([^&<]+)", bloco)

        # contrato vem como CODIGO+matricula (ex: Z8TUR000001, 6 digitos de matricula) -
        # so o prefixo bate com codigo_externo no nosso cadastro
        contrato_bruto = contrato_m.group(1) if contrato_m else None
        contrato_codigo = contrato_bruto[:-6] if contrato_bruto and len(contrato_bruto) > 6 else contrato_bruto

        beneficiario_matricula = usuario_m.group(1) if usuario_m else None
        beneficiario_nome = usuario_m.group(2).strip() if usuario_m else None
        beneficiario_cpf = _normalizar_cpf(cpf_m[0]) if len(cpf_m) > 0 and cpf_m[0] else None
        titular_matricula = titular_m.group(1) if titular_m else None
        titular_nome = titular_m.group(2).strip() if titular_m else None
        titular_cpf = _normalizar_cpf(cpf_m[1]) if len(cpf_m) > 1 and cpf_m[1] else None

        # #DAVJV (04/08): existe uma 2a variante de relatorio (dominio webndi,
        # "Sistema 2-B") sem a coluna "Valor Procedimento" - só Valor
        # Coparticipacao. Tenta a variante com 6 colunas (webhap) primeiro;
        # se o bloco nao tiver nenhum match, tenta a de 5 colunas (webndi).
        proc_matches = list(re.finditer(
            r"<td[^>]*>\s*(\d{6,8})\s*</td>\s*"
            r"<td[^>]*>\s*([^<]+?)\s*</td>\s*"
            r"<td[^>]*>\s*(\d{2}/\d{2}/\d{4})\s*</td>\s*"
            r"<td[^>]*>\s*([^<]+?)\s*</td>\s*"
            r"<td[^>]*>\s*([\d.,]+)\s*</td>\s*"
            r"<td[^>]*>\s*([\d.,]+)\s*</td>",
            bloco,
        ))
        if proc_matches:
            for proc_m in proc_matches:
                codigo, descricao, data_aut, prestador, valor_proc, valor_copart = proc_m.groups()
                linhas.append(
                    {
                        "contrato_codigo": contrato_codigo,
                        "beneficiario_matricula": beneficiario_matricula,
                        "beneficiario_nome": beneficiario_nome,
                        "beneficiario_cpf": beneficiario_cpf,
                        "titular_matricula": titular_matricula,
                        "titular_nome": titular_nome,
                        "titular_cpf": titular_cpf,
                        "codigo_procedimento": codigo,
                        "descricao_procedimento": descricao.strip(),
                        "data_autorizacao": _parse_data_br(data_aut, "%d/%m/%Y"),
                        "nome_prestador": prestador.strip(),
                        "valor_procedimento": _num(valor_proc),
                        "valor_coparticipacao": _num(valor_copart),
                    }
                )
            continue

        for proc_m in re.finditer(
            r"<td[^>]*>\s*(\d{6,8})\s*</td>\s*"
            r"<td[^>]*>\s*([^<]+?)\s*</td>\s*"
            r"<td[^>]*>\s*(\d{2}/\d{2}/\d{4})\s*</td>\s*"
            r"<td[^>]*>\s*([^<]+?)\s*</td>\s*"
            r"<td[^>]*>\s*([\d.,]+)\s*</td>",
            bloco,
        ):
            codigo, descricao, data_aut, prestador, valor_copart = proc_m.groups()
            linhas.append(
                {
                    "contrato_codigo": contrato_codigo,
                    "beneficiario_matricula": beneficiario_matricula,
                    "beneficiario_nome": beneficiario_nome,
                    "beneficiario_cpf": beneficiario_cpf,
                    "titular_matricula": titular_matricula,
                    "titular_nome": titular_nome,
                    "titular_cpf": titular_cpf,
                    "codigo_procedimento": codigo,
                    "descricao_procedimento": descricao.strip(),
                    "data_autorizacao": _parse_data_br(data_aut, "%d/%m/%Y"),
                    "nome_prestador": prestador.strip(),
                    "valor_procedimento": None,
                    "valor_coparticipacao": _num(valor_copart),
                }
            )
    return linhas


def _resolver_eoc_id(cur, codigo_externo):
    cur.execute("SELECT id FROM empresa_operadora_codigos WHERE codigo_externo = %s", (codigo_externo,))
    row = cur.fetchone()
    return row[0] if row else None


def gravar_nota_fiscal_boleto(linhas: list[dict], fonte: str) -> dict:
    if not linhas:
        return {"novos": 0, "atualizados": 0, "rejeitados": 0}
    novos = atualizados = rejeitados = 0
    conn = psycopg2.connect(dbname="lifetools")
    try:
        with conn.cursor() as cur:
            for linha in linhas:
                eoc_id = _resolver_eoc_id(cur, linha["contrato_codigo"])
                if not eoc_id:
                    rejeitados += 1
                    continue
                cur.execute(
                    """
                    INSERT INTO faturas_legado
                        (empresa_operadora_codigo_id, contrato_codigo, pessoa_razao, boleto_numero,
                         vencimento, valor, nfe_pnu, nfe_codigo_verificacao, coletado_em, fonte)
                    VALUES (%(eoc_id)s, %(contrato_codigo)s, %(pessoa_razao)s, %(boleto_numero)s,
                            %(vencimento)s, %(valor)s, %(nfe_pnu)s, %(nfe_codigo_verificacao)s, NOW(), %(fonte)s)
                    ON CONFLICT (contrato_codigo, boleto_numero) DO UPDATE SET
                        valor = EXCLUDED.valor, vencimento = EXCLUDED.vencimento,
                        nfe_pnu = EXCLUDED.nfe_pnu, nfe_codigo_verificacao = EXCLUDED.nfe_codigo_verificacao,
                        coletado_em = NOW(), fonte = EXCLUDED.fonte
                    RETURNING (xmax = 0) AS inserido
                    """,
                    {**linha, "eoc_id": eoc_id, "fonte": fonte},
                )
                if cur.fetchone()[0]:
                    novos += 1
                else:
                    atualizados += 1
        conn.commit()
    finally:
        conn.close()
    return {"novos": novos, "atualizados": atualizados, "rejeitados": rejeitados}


def gravar_coparticipacao(linhas: list[dict]) -> dict:
    if not linhas:
        return {"novos": 0, "atualizados": 0, "rejeitados": 0}
    novos = atualizados = rejeitados = 0
    conn = psycopg2.connect(dbname="lifetools")
    try:
        with conn.cursor() as cur:
            for linha in linhas:
                eoc_id = _resolver_eoc_id(cur, linha["contrato_codigo"]) if linha["contrato_codigo"] else None
                if not eoc_id:
                    rejeitados += 1
                    continue
                cur.execute(
                    """
                    INSERT INTO copart_legado_detalhe
                        (empresa_operadora_codigo_id, contrato_codigo, beneficiario_matricula, beneficiario_nome,
                         beneficiario_cpf, titular_matricula, titular_nome, titular_cpf, codigo_procedimento,
                         descricao_procedimento, data_autorizacao, nome_prestador, valor_procedimento,
                         valor_coparticipacao, coletado_em, primeiro_visto_em)
                    VALUES (%(eoc_id)s, %(contrato_codigo)s, %(beneficiario_matricula)s, %(beneficiario_nome)s,
                            %(beneficiario_cpf)s, %(titular_matricula)s, %(titular_nome)s, %(titular_cpf)s,
                            %(codigo_procedimento)s, %(descricao_procedimento)s, %(data_autorizacao)s,
                            %(nome_prestador)s, %(valor_procedimento)s, %(valor_coparticipacao)s, NOW(), NOW())
                    ON CONFLICT (contrato_codigo, beneficiario_cpf, data_autorizacao, codigo_procedimento) DO UPDATE SET
                        valor_procedimento = EXCLUDED.valor_procedimento,
                        valor_coparticipacao = EXCLUDED.valor_coparticipacao,
                        coletado_em = NOW()
                    RETURNING (xmax = 0) AS inserido
                    """,
                    {**linha, "eoc_id": eoc_id},
                )
                if cur.fetchone()[0]:
                    novos += 1
                else:
                    atualizados += 1
        conn.commit()
    finally:
        conn.close()
    return {"novos": novos, "atualizados": atualizados, "rejeitados": rejeitados}


def checar_status_bemobi(url_sessao: str) -> bool:
    """GET direto na URL de sessao ja autenticada de '2a via de Empresa - Faturas'
    (webNewBoletoEmpresa.obrigacaoListaBemobi). Achado real 20/08 (Andre, contrato
    S7HNK): a pagina so tem 2 estados - se existe boleto em aberto, ela embute um
    iframe com o widget de pagamento da Bemobi (invoicescheckout.bemobi.com); se
    nao existe nada pendente, o iframe nao aparece. Nao devolve valor/vencimento
    (isso fica dentro do iframe, que e' um app JS separado, nao renderiza num GET
    simples) - so o binario tem-pendencia/nao-tem.

    Achado anterior (04/08, ver Addendum 10 no topo do arquivo) ja tinha achado
    esse mesmo iframe mas interpretou como fonte alternativa de PDF do boleto
    (travou tentando escalar o token de pagamento da Bemobi, 403) - nao tinha
    percebido que a PRESENCA do iframe em si ja e' o sinal de status.

    Retorna True se ha' boleto em aberto (iframe presente), False se liquidado
    (pagina sem iframe)."""
    r = httpx.get(url_sessao, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return "invoicescheckout.bemobi.com" in r.text


def gravar_status_bemobi(contrato_codigo: str, tem_pendencia: bool) -> dict:
    """Aplica o status na fatura MAIS RECENTE do contrato (mesmo criterio do
    mapeamento Grupo Saude, #117 Addendum 9) - a pagina Bemobi nao diz QUAL
    fatura esta em aberto, so que o contrato tem (ou nao) algo pendente agora."""
    status = "Em Aberto" if tem_pendencia else "Liquidado"
    conn = psycopg2.connect(dbname="lifetools")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE faturas_legado SET status = %s
                WHERE contrato_codigo = %s
                  AND vencimento = (
                      SELECT MAX(vencimento) FROM faturas_legado WHERE contrato_codigo = %s
                  )
                """,
                (status, contrato_codigo, contrato_codigo),
            )
            atualizadas = cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return {"contrato_codigo": contrato_codigo, "status": status, "faturas_atualizadas": atualizadas}


def processar_sessao_legado(
    url_nota_fiscal: str = None,
    url_coparticipacao: str = None,
    url_status_boleto: str = None,
    contrato_codigo: str = None,
    fonte: str = "manual",
) -> dict:
    """Ponto de entrada unico: recebe URLs de sessao ja autenticada (Andre
    passa manualmente), busca e grava o que existir. Nao tenta logar - se a
    URL/sessao estiver expirada, a chamada falha e isso aparece no resultado.

    url_status_boleto: URL da pagina Bemobi (webNewBoletoEmpresa.obrigacaoListaBemobi).
    Precisa de contrato_codigo junto (a URL nao tem o codigo em formato
    parseavel de forma confiavel em todos os casos - mais seguro pedir
    explicito do que tentar extrair de pCd_Emp_Conv)."""
    resultado = {}
    if url_nota_fiscal:
        try:
            linhas = buscar_nota_fiscal_boleto(url_nota_fiscal)
            resultado["nota_fiscal_boleto"] = {"linhas_encontradas": len(linhas), **gravar_nota_fiscal_boleto(linhas, fonte)}
        except Exception as e:
            resultado["nota_fiscal_boleto"] = {"erro": str(e)}
    if url_coparticipacao:
        try:
            linhas = buscar_coparticipacao(url_coparticipacao)
            resultado["coparticipacao"] = {"linhas_encontradas": len(linhas), **gravar_coparticipacao(linhas)}
        except Exception as e:
            resultado["coparticipacao"] = {"erro": str(e)}
    if url_status_boleto:
        if not contrato_codigo:
            resultado["status_boleto"] = {"erro": "contrato_codigo obrigatorio junto com url_status_boleto"}
        else:
            try:
                tem_pendencia = checar_status_bemobi(url_status_boleto)
                resultado["status_boleto"] = gravar_status_bemobi(contrato_codigo, tem_pendencia)
            except Exception as e:
                resultado["status_boleto"] = {"erro": str(e)}
    return resultado
