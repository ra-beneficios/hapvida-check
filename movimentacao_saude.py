"""
#150 - Envio de movimentacao (Via arquivo / layout) no portal-empresa moderno da Hapvida.
Motor por baixo do endpoint /run-inclusao, chamado pela Cora depois de /converter.

Historico da investigacao completa (nao redescobrir - ver tambem
scripts_cowork/patch2_modalidade/142_procedimento_inclusao_portal_moderno_gndi.md e
150_procedimento_inclusao_hapvida_protocolo.md no repo lifetools):

- Checar o status logo apos o upload e enganoso: sempre mostra "UnableToStart"/"Impossivel
  iniciar" transitorio, mesmo em uploads que depois viram "FileProcessedWithReview" (sucesso)
  ou "ProcessFileWithPendency" (processou com criticas reais) horas depois. Por isso esta
  funcao PARA no 200 OK do upload - nao checa status de processamento.
- 829 chars/linha (sem rstrip em gerar_linha.py) e' o formato correto - nao mexer.
- Campo 129 (municipio) usa radb.municipios (6 digitos), nunca a API do IBGE.
- Achado #150 (01/09): o portal agora exige selecionar a operadora ("NDI") num dropdown
  ANTES do botao Buscar habilitar - sem isso a busca por codigo de contrato nunca mostra
  resultado, mesmo com o texto ja preenchido via query string.
- O locator generico por "codigo do contrato" (tr:has-text/div:has-text) e' fragil - ja
  bateu num div grande demais mais de uma vez. O fallback confiavel e' localizar a linha
  pelo nome da empresa (razao social), visivel na tela apos a busca.
- Achado real (#154, 04/09): a tela "Via arquivo (layout)" ja mostra o status legivel de cada
  movimentacao (Finalizado com criticas / Processamento com pendencia / etc) sem precisar
  clicar em nada - e por baixo tem uma API REST de verdade (api.hapvida.com.br/b2b/api/v3/
  movements/...), autenticada com Bearer token + header ocp-apim-subscription-key que o
  proprio browser manda nas chamadas normais. Da pra capturar esses headers (ver
  _capturar_headers_api) e chamar a API direto - muito mais robusto que raspar a tela pra
  checagem de status (checar_status_movimentacoes) e pra baixar o relatorio de criticas
  (baixar_relatorio_criticas), sem precisar reproduzir clique de UI nenhum.
"""
import re
import time

from playwright.sync_api import sync_playwright

from scraper import get_credentials

LOGIN_URL = "https://portal-empresa.hapvidagndi.com.br"
ESPERA_SESSAO_ASSENTAR_MS = 9000


def _localizar_e_entrar_no_contrato(page, codigo_contrato: str) -> None:
    # captura a resposta de companies?search=<codigo> pra pegar a razao social (campo
    # "legal") - clicar pelo nome da empresa e' o padrao confiavel ja validado varias
    # vezes (#150); clicar direto no codigo ou em locators genericos ja falhou (matcheou
    # elemento errado ou deu timeout).
    empresa_info: dict = {}

    def on_response(response):
        if "companies?search=" in response.url:
            try:
                body = response.json()
                empresas = body.get("content", {}).get("companies", [])
                if empresas:
                    empresa_info["legal"] = empresas[0]["legal"]
            except Exception:
                pass

    page.on("response", on_response)

    page.goto(f"{LOGIN_URL}/contratos?search={codigo_contrato}", timeout=30000)
    page.wait_for_load_state("networkidle", timeout=20000)
    page.wait_for_timeout(2000)

    if page.locator("text=Selecione a operadora").count() > 0:
        page.click("text=Selecione a operadora")
        page.wait_for_timeout(1000)
        page.click("text=NDI")
        page.wait_for_timeout(1500)
        page.click("text=Buscar")
        page.wait_for_timeout(2500)

    if not empresa_info.get("legal"):
        raise RuntimeError(
            f"Contrato {codigo_contrato} nao encontrado na busca (razao social nao veio "
            f"da API companies?search=)."
        )

    nome_empresa = empresa_info["legal"]
    row = page.locator(f"text={nome_empresa}").first
    row.wait_for(state="visible", timeout=15000)
    box = row.bounding_box()
    if not box:
        raise RuntimeError(f"Linha da empresa '{nome_empresa}' nao ficou visivel.")

    row.click()
    page.wait_for_timeout(1200)
    # a linha so navega pelo icone/seta no canto direito - clicar no texto so
    # seleciona/expande visualmente.
    page.mouse.click(1374, box["y"] + 5)
    page.wait_for_timeout(2000)
    page.wait_for_load_state("networkidle", timeout=20000)
    page.wait_for_timeout(ESPERA_SESSAO_ASSENTAR_MS)


def enviar_movimentacao(usuario: str, senha: str, codigo_contrato: str, arquivo_txt_path: str) -> dict:
    """Fluxo completo: login -> localizar contrato -> Movimentacoes -> Via arquivo (layout) ->
    Nova movimentacao -> upload -> confirmar no modal. PARA no 200 OK do upload (ver notas
    no topo do arquivo sobre por que nao checar status depois)."""
    resultado: dict = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        def on_response(response):
            if "attachments" in response.url and response.request.method == "POST":
                resultado["status"] = response.status
                try:
                    resultado["body"] = response.json()
                except Exception as e:
                    resultado["erro_parse"] = str(e)

        page.on("response", on_response)

        import portal_vez
        portal_vez.registrar("LOGIN", codigo_contrato)   # obra 3 (observando): login do envio pela tela
        page.goto(LOGIN_URL, timeout=30000)
        page.wait_for_load_state("networkidle", timeout=20000)
        page.fill("#email", usuario)
        page.fill("#password", senha)
        page.click("#next")
        page.wait_for_timeout(4000)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        _localizar_e_entrar_no_contrato(page, codigo_contrato)

        try:
            page.click("text=Movimentações")
            page.wait_for_timeout(1500)
            page.click("text=Via arquivo (layout)")
            page.wait_for_timeout(3000)
            page.wait_for_load_state("networkidle", timeout=20000)
            page.wait_for_timeout(2500)

            # Achado real (PEG8N, 04/09): falha transitoria do proprio portal - tela "Atencao!
            # Nao conseguimos carregar a pagina." com botao "Recarregar pagina". Nao e' bug
            # nosso, e' o servidor deles engasgando (provavel limite de taxa, muitos logins
            # seguidos no mesmo dia) - clica em recarregar e tenta de novo antes de desistir.
            for _tentativa in range(2):
                if page.locator("text=Não conseguimos carregar a página").count() == 0:
                    break
                page.click("text=Recarregar página")
                page.wait_for_timeout(3000)
                page.wait_for_load_state("networkidle", timeout=20000)
                page.wait_for_timeout(2500)

            # Achado real (DAVJV/FHMWR, 04/09): empresa sem NENHUMA movimentacao anterior mostra
            # um estado vazio com botao "Movimentar beneficiarios agora" em vez de "Nova
            # movimentacao" (que so aparece quando ja existe pelo menos uma movimentacao anterior
            # na lista) - aceita os dois. locator1.or_(locator2) (nao string com virgula) - o
            # comma-string so foi validado nesse arquivo pra selectors CSS, nao pro engine text=.
            btn_nova = page.locator("text=Nova movimentação")
            btn_vazio = page.locator("text=Movimentar beneficiários agora")
            btn_nova.or_(btn_vazio).first.click(timeout=15000)
            page.wait_for_timeout(2500)
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(1500)

            page.set_input_files("input[type=file]", arquivo_txt_path)
            page.wait_for_timeout(3000)
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(4000)

            page.click("button:has-text('Enviar movimentação')")
            page.wait_for_timeout(1500)
            modal_btn = page.locator(
                "div[role='dialog'] button:has-text('Enviar movimentação'), "
                ".modal button:has-text('Enviar movimentação')"
            )
            if modal_btn.count() == 0:
                modal_btn = page.locator("button:has-text('Enviar movimentação')").last
            # Achado real (exclusao 2XP6W, 02/10): o POST .../attachments responde DEPOIS dos 3s fixos que
            # havia aqui - o upload entrou (protocolo 1330271) mas o resultado voltou sem status/body. Espera a
            # resposta de verdade; se nao vier em 60s, segue sem ela (a checagem por movements-list confirma).
            try:
                with page.expect_response(
                    lambda r: "attachments" in r.url and r.request.method == "POST", timeout=60000
                ) as resp_info:
                    modal_btn.click()
                resp = resp_info.value
                resultado["status"] = resp.status
                try:
                    resultado["body"] = resp.json()
                except Exception as e:
                    resultado["erro_parse"] = str(e)
            except Exception as e:
                resultado["sem_resposta_upload"] = str(e)[:200]

            page.wait_for_timeout(3000)
            resultado["url_final"] = page.url
        except Exception:
            # Diagnostico: se qualquer passo depois de entrar no contrato falhar, salva
            # screenshot + texto visivel antes de fechar o browser - sem isso nao da pra
            # saber remotamente em que tela o fluxo travou (achado real, contrato FHMWR
            # 04/09: timeout no "Nova movimentacao" sem pista do motivo).
            try:
                page.screenshot(path="/tmp/erro_movimentacao.png", full_page=True)
                with open("/tmp/erro_movimentacao.txt", "w") as f:
                    f.write(page.url + "\n\n" + page.locator("body").inner_text())
            except Exception:
                pass
            raise

        browser.close()

    return resultado


# DAVJV (UTC Óleo e Gás) só é visível na conta da Marco no portal — achado real da apuração
# XFP6J/B6PJX (ver scripts_cowork/patch2_modalidade/153_xfp6j_superado_por_b6pjx.md). Qualquer
# outro contrato usa a conta padrão da Carol.
# 15/10/2026 (André): conta do sistema = Cora em TODOS os contratos (testado: Cora entra nas movimentações do DAVJV).
# A conta do Vinicios (hapvida-portal-saude-marco) fica só de backup. Conjunto vazio = ninguém usa mais o backup aqui.
CONTRATOS_CONTA_MARCO: set[str] = set()


def run_inclusao(project: str, codigo_contrato: str, arquivo_txt_path: str) -> dict:
    """Ponto único de upload ao portal (inclusão da Cora, reenvio do motor, exclusão). 17/10/2026 (obra 2): registra em
    portal.envio ANTES de subir e o resultado depois — reiniciar no meio não perde nem duplica (portal_envio.py)."""
    import portal_envio
    eid = portal_envio.antes(codigo_contrato, arquivo_txt_path)   # levanta portal_envio.Duplicado nos tipos que bloqueiam
    secret_id = "hapvida-portal-saude-marco" if codigo_contrato.upper() in CONTRATOS_CONTA_MARCO else "hapvida-portal-saude"
    creds = get_credentials(project, secret_id)
    resultado = enviar_movimentacao(creds["usuario"], creds["senha"], codigo_contrato, arquivo_txt_path)
    portal_envio.depois(eid, resultado)
    return resultado


def _credenciais_por_contrato(project: str, codigo_contrato: str) -> dict:
    secret_id = "hapvida-portal-saude-marco" if codigo_contrato.upper() in CONTRATOS_CONTA_MARCO else "hapvida-portal-saude"
    return get_credentials(project, secret_id)


def _capturar_headers_api(page) -> dict:
    """Captura os headers de autenticação (Bearer token + subscription key) que o próprio
    portal usa nas chamadas pra api.hapvida.com.br - permite chamar a API direto em vez de
    raspar a tela (ver achado #154 no topo do arquivo)."""
    headers_capturados: dict = {}

    def on_request(req):
        if "api.hapvida.com.br" in req.url and "authorization" in req.headers:
            headers_capturados["authorization"] = req.headers["authorization"]
            if "ocp-apim-subscription-key" in req.headers:
                headers_capturados["ocp-apim-subscription-key"] = req.headers["ocp-apim-subscription-key"]

    page.on("request", on_request)
    page.click("text=Movimentações")
    page.wait_for_timeout(1500)
    page.click("text=Via arquivo (layout)")
    page.wait_for_timeout(3000)
    page.wait_for_load_state("networkidle", timeout=20000)
    page.wait_for_timeout(2000)
    return headers_capturados


def _login_e_capturar_headers(page, usuario: str, senha: str, codigo_contrato: str) -> dict:
    import portal_vez
    portal_vez.registrar("LOGIN", codigo_contrato)   # obra 3 (observando)
    page.goto(LOGIN_URL, timeout=30000)
    page.wait_for_load_state("networkidle", timeout=20000)
    page.fill("#email", usuario)
    page.fill("#password", senha)
    page.click("#next")
    page.wait_for_timeout(4000)
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    _localizar_e_entrar_no_contrato(page, codigo_contrato)
    headers = _capturar_headers_api(page)
    _proteger_contrato(page, codigo_contrato)
    return headers


CONTRATO_TROCADO = "selecione novamente seu contrato"


def _proteger_contrato(page, codigo_contrato: str, tentativas: int = 2) -> None:
    """17/10/2026 (achado real): o portal guarda UM contrato selecionado por usuário. Todas as automações entram com a
    mesma conta (Cora); quando duas rodam juntas em contratos diferentes (vigia de 15 em 15 min + motor + upload), a
    outra sessão troca o contrato e a nossa passa a receber 400 "O contrato da sua página foi atualizado. Selecione
    novamente seu contrato" — era a causa dos 400 "transitórios" (lista de movimentações, pedidos via tela, life-data).
    Daqui em diante, toda chamada da página que receber esse 400 seleciona o contrato de novo e repete."""
    req = page.request
    if getattr(req, "_contrato_protegido", None) == codigo_contrato:
        return

    def embrulhar(metodo):
        original = getattr(req, metodo)

        def chamada(*a, **k):
            r = original(*a, **k)
            for _ in range(tentativas):
                if r.status != 400:
                    return r
                try:
                    trocado = CONTRATO_TROCADO in (r.text() or "").lower()
                except Exception:
                    trocado = False
                if not trocado:
                    return r
                print(f"[sessao] contrato {codigo_contrato} trocado por outra sessão da mesma conta - selecionando de novo")
                import portal_vez
                portal_vez.registrar("RESELECAO", codigo_contrato)
                _localizar_e_entrar_no_contrato(page, codigo_contrato)
                r = original(*a, **k)
            return r
        return chamada

    for metodo in ("get", "post", "put", "delete", "fetch"):
        setattr(req, metodo, embrulhar(metodo))
    req._contrato_protegido = codigo_contrato


def checar_status_movimentacoes(project: str, codigo_contrato: str, limit: int = 10) -> dict:
    """Lista as movimentações recentes do contrato (JSON real da API, sem raspar tela) - usado
    pra checagem inicial (o upload realmente registrou?) e pra achar o status atual da
    movimentação mais recente. Formato de cada item vem direto da API da Hapvida."""
    creds = _credenciais_por_contrato(project, codigo_contrato)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        headers = _login_e_capturar_headers(page, creds["usuario"], creds["senha"], codigo_contrato)
        resp = page.request.get(
            f"https://api.hapvida.com.br/b2b/api/v3/movements/partner-company/{codigo_contrato}/movements-list"
            f"?offset=0&limit={limit}",
            headers=headers,
        )
        dados = resp.json() if resp.ok else {"erro_http": resp.status, "texto": resp.text()[:500]}
        browser.close()
    return dados


def baixar_arquivo_original_movimentacao(project: str, codigo_contrato: str, numero_controle: str) -> bytes | None:
    """Baixa o CONTEÚDO ORIGINAL (829 chars/linha) que foi enviado numa movimentação, pelo
    número de controle - NÃO é o relatório de críticas (achado real, #155: esse endpoint
    devolve o arquivo em si, não os erros - ver baixar_criticas_movimentacao pra críticas de
    verdade). Útil pra auditoria/conferência do que foi realmente processado."""
    creds = _credenciais_por_contrato(project, codigo_contrato)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        headers = _login_e_capturar_headers(page, creds["usuario"], creds["senha"], codigo_contrato)
        resp = page.request.get(
            f"https://api.hapvida.com.br/b2b/api/v3/movements/partner-company/{codigo_contrato}"
            f"/control-number/{numero_controle}/download/life-movement-sib",
            headers=headers,
        )
        conteudo = resp.body() if resp.ok else None
        browser.close()
    return conteudo


def baixar_criticas_movimentacao(
    project: str, codigo_contrato: str, numero_controle: str, limit: int = 500
) -> list[dict]:
    """#155 - achado real (09/09): o relatório de críticas de verdade (linha a linha, com o
    texto exato do erro) vem do endpoint "shipping-reviews", achado clicando em "mostrar
    detalhes" na tela "Via arquivo (layout)" e capturando a chamada real. Retorna a lista de
    críticas (cada item tem pelo menos "line"/numero da linha e o texto do erro) - lista vazia
    se não tiver nenhuma."""
    creds = _credenciais_por_contrato(project, codigo_contrato)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        headers = _login_e_capturar_headers(page, creds["usuario"], creds["senha"], codigo_contrato)
        resp = page.request.get(
            f"https://api.hapvida.com.br/b2b/api/v3/movements/partner-company/{codigo_contrato}"
            f"/control-number/{numero_controle}/shipping-reviews?offset=0&limit={limit}",
            headers=headers,
        )
        dados = resp.json() if resp.ok else {"erro_http": resp.status}
        browser.close()
    conteudo = dados.get("content")
    if isinstance(conteudo, dict):
        # campo real confirmado (WP64K/1324201, 09/09): {"content": {"reviews": [{"row",
        # "sequential", "message", "errorCode"}, ...], "total", "totalPerPage"}}
        return conteudo.get("reviews") or []
    if isinstance(conteudo, list):
        return conteudo
    return []


def levantar_criticas_contrato(project: str, codigo_contrato: str, tentativas: int = 3) -> list[dict]:
    """#155 - varre TODAS as movimentações de um contrato e traz as críticas de cada uma que
    tiver totalShippingReviews > 0. Retorna lista de dicts:
    {controlCode, uploadName, createdAt, status, criticas: [...]}.

    Faz UM login só por contrato e reaproveita a sessão pra a lista + todas as críticas
    (antes era 1 login por movimentação - inviável pra varrer os 34 contratos). Faz retry
    (padrão 3x) quando o portal responde o 400 transitório "contrato foi atualizado,
    selecione novamente" - achado real (09/09), aparece em login em sequência rápida."""
    creds = _credenciais_por_contrato(project, codigo_contrato)

    for tentativa in range(tentativas):
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            try:
                headers = _login_e_capturar_headers(page, creds["usuario"], creds["senha"], codigo_contrato)

                resp_lista = page.request.get(
                    f"https://api.hapvida.com.br/b2b/api/v3/movements/partner-company/{codigo_contrato}"
                    f"/movements-list?offset=0&limit=100",
                    headers=headers,
                )
                if not resp_lista.ok:
                    if tentativa < tentativas - 1:
                        continue
                    return [{"erro": f"movements-list HTTP {resp_lista.status}: {resp_lista.text()[:300]}"}]

                movimentos = (resp_lista.json().get("content") or {}).get("movements") or []
                resultado = []
                for m in movimentos:
                    total_reviews = int(m.get("totalShippingReviews") or 0)
                    entrada = {
                        "controlCode": m.get("controlCode"),
                        "uploadName": m.get("uploadName"),
                        "createdAt": m.get("createdAt"),
                        "status": m.get("status"),
                        "totalShippingReviews": total_reviews,
                        "criticas": [],
                    }
                    if total_reviews > 0:
                        # shipping-reviews: limite MAXIMO 200 por pagina (achado real 09/09:
                        # limit=1000 dava 400 "O valor maximo de busca e 200"). Pagina por
                        # offset ate juntar tudo (2XP6W tem movimentacao com 285 criticas).
                        todas = []
                        offset = 0
                        while offset < total_reviews:
                            resp_rev = page.request.get(
                                f"https://api.hapvida.com.br/b2b/api/v3/movements/partner-company/{codigo_contrato}"
                                f"/control-number/{m['controlCode']}/shipping-reviews?offset={offset}&limit=200",
                                headers=headers,
                            )
                            if not resp_rev.ok:
                                break
                            pagina = (resp_rev.json().get("content") or {}).get("reviews") or []
                            if not pagina:
                                break
                            todas.extend(pagina)
                            offset += 200
                        entrada["criticas"] = todas
                    resultado.append(entrada)
                return resultado
            finally:
                browser.close()

    return [{"erro": "falhou apos todas as tentativas"}]


def levantar_vidas_criticadas_contrato(project: str, codigo_contrato: str, tentativas: int = 3) -> list[dict]:
    """#155 v2 - achado real (11/09, feedback do André): shipping-reviews só pega uma
    categoria estreita de crítica (nível de linha/arquivo, ex "acentuação" - na real,
    pontuação no endereço). A crítica de verdade, acionável (nome, CPF, regra de negócio tipo
    "Data de casamento incompatível com data de nascimento"), vem de "lifes-with-summary" -
    mesma API por trás da tela .../movimentacoes/{controle}?review=true. Retorna, por
    movimentação, a lista de vidas com status != "Processed" (ou seja "Reviewed"=criticada,
    "Canceled"=cancelada), com nome/CPF/tipo/mensagens de negócio reais."""
    creds = _credenciais_por_contrato(project, codigo_contrato)

    for tentativa in range(tentativas):
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            try:
                headers = _login_e_capturar_headers(page, creds["usuario"], creds["senha"], codigo_contrato)

                resp_lista = page.request.get(
                    f"https://api.hapvida.com.br/b2b/api/v3/movements/partner-company/{codigo_contrato}"
                    f"/movements-list?offset=0&limit=100",
                    headers=headers,
                )
                if not resp_lista.ok:
                    if tentativa < tentativas - 1:
                        continue
                    return [{"erro": f"movements-list HTTP {resp_lista.status}: {resp_lista.text()[:300]}"}]

                movimentos = (resp_lista.json().get("content") or {}).get("movements") or []
                resultado = []
                for m in movimentos:
                    vidas_criticadas = _buscar_vidas_criticadas(page, headers, codigo_contrato, m["controlCode"])
                    resultado.append(
                        {
                            "controlCode": m.get("controlCode"),
                            "uploadName": m.get("uploadName"),
                            "createdAt": m.get("createdAt"),
                            "status": m.get("status"),
                            "vidasCriticadas": vidas_criticadas,
                        }
                    )
                return resultado
            finally:
                browser.close()

    return [{"erro": "falhou apos todas as tentativas"}]


def _buscar_vidas_criticadas(page, headers: dict, codigo_contrato: str, control_code) -> list[dict]:
    """Paginação de lifes-with-summary pra UMA movimentação específica - fatorado de
    levantar_vidas_criticadas_contrato pra ser reaproveitado por vidas_criticadas_de_movimentacao
    (checagem de 12h do #156, uma movimentação por vez, sem precisar logar de novo)."""
    vidas_criticadas: list[dict] = []
    offset = 0
    while True:
        resp = page.request.get(
            f"https://api.hapvida.com.br/b2b/api/v3/movements/partner-company/{codigo_contrato}"
            f"/control-code/{control_code}/lifes-with-summary?offset={offset}&limit=200&review=true",
            headers=headers,
        )
        if not resp.ok:
            break
        conteudo = resp.json().get("content") or {}
        pagina = conteudo.get("data") or []
        total = conteudo.get("total", 0)
        for item in pagina:
            if item.get("status") != "Processed":
                vida = item.get("life") or {}
                vidas_criticadas.append(
                    {
                        "status": item.get("status"),
                        "operacao": item.get("operation"),
                        "nome": vida.get("name"),
                        "cpf": vida.get("document"),
                        "tipo": vida.get("beneficiaryType"),
                        "sequencial": item.get("sequentialNumber"),
                        "mensagens": item.get("reviews") or [],
                    }
                )
        offset += 200
        if offset >= total or not pagina:
            break
    return vidas_criticadas


def vidas_criticadas_de_movimentacao(project: str, codigo_contrato: str, control_code, tentativas: int = 3) -> dict:
    """#156 - checagem de 12h: busca as vidas criticadas/canceladas de UMA movimentação
    específica (não do contrato inteiro), pra mandar no relatório final pro solicitante."""
    creds = _credenciais_por_contrato(project, codigo_contrato)
    for tentativa in range(tentativas):
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            try:
                headers = _login_e_capturar_headers(page, creds["usuario"], creds["senha"], codigo_contrato)
                vidas = _buscar_vidas_criticadas(page, headers, codigo_contrato, control_code)
                return {"vidasCriticadas": vidas}
            except Exception as e:
                if tentativa < tentativas - 1:
                    continue
                return {"erro": str(e)}
            finally:
                browser.close()
    return {"erro": "falhou apos todas as tentativas"}
