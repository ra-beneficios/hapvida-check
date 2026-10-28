"""Odonto Premium no Legado (SIGO) chamado pelo Portal de Movimentações (28/10/2026).

Mesma lógica das ferramentas de odonto_legado/ (lote_formulario_tpl.py e precancelar.py, usadas em 22–23/10 no HC2 e
outros), agora como função: o portal manda as famílias já corrigidas pelo motor (as mesmas da Saúde) e o resultado
volta por CPF. Regras (André, 27/10): o Premium sobe junto com a Saúde, só pra quem subiu limpo nela; Saúde que não
entrou -> pré-cancelamento (motivo 50, questionário A/N/N, sem e-mail; o Legado efetiva no 1º dia do ciclo seguinte).

Login por contrato (código + senha do Legado, Bitwarden -> cofre), não usa a conta da Cora do portal moderno.
O resultado de cada titular fica em /dados_motor/odonto_premium/<COD>/: chamada repetida não cadastra de novo quem já
foi (FALHA pode tentar de novo).
"""
import json
import os
import re
import time
from pathlib import Path

import censo_legado
import secrets_store

PLANO = "9732"
DADOS = Path(os.environ.get("CICLO_DADOS", "/dados_motor")) / "odonto_premium"
_txt = lambda t: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(style|script|select)[^>]*>.*?</\1>", " ", t)))


def _pasta(cod: str) -> Path:
    p = DADOS / cod
    p.mkdir(parents=True, exist_ok=True)
    os.chmod(DADOS, 0o700)
    return p


def _gravar(caminho: Path, dados) -> None:
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=1))
    os.chmod(caminho, 0o600)


def _login(page, cod: str) -> str:
    cred = secrets_store.get_json("hapvida-legado")[cod]
    page.goto(censo_legado.LOGIN_URL, timeout=30000)
    page.wait_for_load_state("networkidle")
    page.fill("input[name='pCodigoEmpresa']", cod)
    page.keyboard.press("Tab")
    page.wait_for_selector("input[name='pSenha']", state="visible", timeout=10000)
    page.fill("input[name='pSenha']", cred["senha"])
    page.click("#Prosseguir")
    page.wait_for_url(re.compile("pIdSessao="), timeout=30000)
    page.wait_for_timeout(2000)
    return page.url


def _setv(page, sel, v):
    if v:
        page.evaluate("([s, v]) => { const e = document.querySelector(s); if (!e.value) e.value = v; }", [sel, v])


def _enviar(page):
    with page.expect_navigation(timeout=60000):
        page.click("#btProsseguir")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2500)


def _titular(page, menu: str, f: dict, html: Path) -> str:
    page.goto(menu)
    page.wait_for_load_state("networkidle")
    m = re.search(r"(webNewCadastroUsuario\.InclusaoContratoCpf\?[^'\"\s]+)", page.content())
    page.goto(page.url.split("/pls/")[0] + "/pls/webhap/" + m.group(1).replace("&amp;", "&"))
    page.wait_for_load_state("networkidle")
    page.fill("input[name=pCpfTitular]", f["cpf"])
    with page.expect_navigation(timeout=30000):
        page.evaluate("document.forms[0].submit()")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(3000)
    if "inclusaoContratoFicha" not in page.url or not page.query_selector("#pNomeTitular"):
        t = _txt(page.content())
        i = t.find("DATA:")
        return "BLOQUEADO: " + (t[i:i + 300] if i >= 0 else t[-300:])
    _setv(page, "#pNomeTitular", f["nome"][:45])
    # 017: a Receita abrevia o nome da mãe (ex.: "DULCELY DE B QUEIROZ"); com o nome completo, sobrescreve
    page.evaluate("""m => { const e = document.querySelector('[name=pMae]'); if (!e || !m) return;
        if (!e.value || / [A-Z]\\.? /.test(' ' + e.value + ' ')) { e.removeAttribute('readonly'); e.value = m; } }""",
                  (f.get("mae") or "")[:45])
    page.select_option("#pSexo", f["sexo"])
    page.select_option("#pEstadoCivil", f["ec"])
    page.click("#pDataAdm2")
    page.keyboard.type(f["adm"])
    page.keyboard.press("Tab")
    page.wait_for_timeout(2500)
    page.select_option("#pUnidade", "1")
    page.wait_for_timeout(2500)
    page.select_option("#pPlano", PLANO)
    page.click("#pCep")
    page.keyboard.type(f["cep"])
    page.keyboard.press("Tab")
    page.wait_for_timeout(3000)
    page.select_option("#pLogradouro", f["tlog"])
    for k in ("pEndereco", "pNumero", "pCompl", "pBairro", "pCidade"):
        page.fill("#" + k, (f.get(k) or "")[:60])
    page.select_option("#pUf", f["pUf"])
    page.evaluate("""() => { const o = document.querySelector('[name=pOrgao]'), u = document.querySelector('[name=pUfOrgao]');
        const m = (o && o.value || '').toUpperCase().match(/(AC|AL|AM|AP|BA|CE|DF|ES|GO|MA|MG|MS|MT|PA|PB|PE|PI|PR|RJ|RN|RO|RR|RS|SC|SE|SP|TO)$/);
        if (m && u && !u.value) u.value = m[1]; }""")
    page.evaluate("() => { const o = document.querySelector('[name=pOrgao]'), u = document.querySelector('[name=pUfOrgao]'); "
                  "if (!o || !o.value || !u || !u.value) { document.querySelector('[name=pRg]').value = ''; o.value = ''; u.value = ''; } }")
    _enviar(page)
    (html / f"tit_{f['cpf']}.html").write_text(page.content())
    t = _txt(page.content())
    i = t.find("DATA:")
    t = t[i:i + 400] if i >= 0 else t[:400]
    if "TITULAR FOI CADASTRADO COM SUCESSO" in t:
        return "OK"
    if "PROCESSO DE VALIDA" in t:
        m = re.search(r"CADASTRO EM PROCESSO[^.]*?(?= Hapvida Institucional|$)", t)
        return "PENDENTE_DOC: " + (m.group(0) if m else t[:200])
    return "FALHA: " + t


def _dependente(page, d: dict, html: Path, alertas: list) -> str:
    if d["tipo"].startswith("?"):
        return "PULADO: parentesco " + d["tipo"]
    page.click("#pCpf")
    page.keyboard.type(d["cpf"])
    page.keyboard.press("Tab")
    page.wait_for_timeout(3500)
    _setv(page, "#pNomeDep", d["nome"][:45])
    _setv(page, "#pDataNasc", d["nasc"])
    _setv(page, "#pMae", d["mae"][:45])
    page.select_option("#pEstadoCivil", d["ec"])
    page.select_option("#pTipoDep", d["tipo"])
    page.select_option("#pSexo", d["sexo"])
    # 158: a ficha guarda a data de casamento do dependente anterior; sempre grava a deste (vazia se não for cônjuge)
    page.evaluate("v => { const e = document.querySelector('#pDtCasamento'); if (e) e.value = v; }",
                  d["cas"] if d["tipo"] == "1" else "")
    page.evaluate("carregaVigenciaCargoDep(document.getElementById('pDataNasc'))")
    page.wait_for_timeout(2000)
    n = len(alertas)
    _enviar(page)
    (html / f"dep_{d['cpf']}.html").write_text(page.content())
    t = _txt(page.content())
    m = re.search(r"\*\s*\*\s*\*([^*]{5,200})\*\s*\*\s*\*", t)
    return (m.group(1).strip() if m else t[-300:]) + (" | ALERTAS: " + "; ".join(alertas[n:]) if alertas[n:] else "")


def incluir(cod: str, familias: list[dict]) -> dict:
    """Formulário do Legado: titular + dependentes, família por família. -> {cpf_titular: {titular, deps: {cpf: msg}}}"""
    from playwright.sync_api import sync_playwright
    pasta = _pasta(cod)
    html = pasta / "html"
    html.mkdir(exist_ok=True)
    arq = pasta / "lote.json"
    try:
        res = json.loads(arq.read_text())
    except Exception:
        res = {}
    alertas = []
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page()
        page.on("dialog", lambda dg: (alertas.append(dg.message), dg.accept()))
        try:
            menu = _login(page, cod)
            for f in familias:
                if f["cpf"] in res and not res[f["cpf"]]["titular"].startswith("FALHA"):
                    continue        # já foi: não cadastra de novo
                r = {"titular": _titular(page, menu, f, html), "deps": {}}
                if r["titular"] == "OK" or r["titular"].startswith("PENDENTE_DOC"):
                    # 28/10/2026 (UTC SY8MI): o "em validação" também já tem controle (é por ele que o documento entra)
                    r["controle"] = page.evaluate("document.querySelector('input[name=pControle]')?.value")
                if r["titular"] == "OK":
                    for d in f["deps"]:
                        r["deps"][d["cpf"]] = _dependente(page, d, html, alertas)
                        if "inclusaodependente" not in page.url.lower():
                            break   # saiu da ficha de dependente: os outros ficam sem resposta (voltam como recusados)
                res[f["cpf"]] = r
                _gravar(arq, res)
        finally:
            b.close()
    return {f["cpf"]: res.get(f["cpf"]) for f in familias if f["cpf"] in res}


def pre_cancelar(cod: str, acoes: list[list]) -> dict:
    """acoes: [[CANCELA_CONTRATO|CANCELA_DEP, cpf, nome, cpf_titular]]. -> {cpf: 'OK ...' | 'SEM_CODIGO' | 'FALHA: ...'}"""
    from playwright.sync_api import sync_playwright
    pasta = _pasta(cod) / "excl"
    pasta.mkdir(exist_ok=True)
    arq = pasta / "res.json"
    try:
        res = json.loads(arq.read_text())
    except Exception:
        res = {}
    txt = lambda page: re.sub(r"\s+", " ", page.locator("body").inner_text())

    def dump(page, tag):
        (pasta / f"{tag}.html").write_text(page.content())
        t = txt(page)
        i = t.find("DATA:")
        return t[i:i + 700]

    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        page = b.new_page()
        page.on("dialog", lambda d: d.accept())
        try:
            _login(page, cod)
            pessoa = re.search(r"pPessoa=(\d+)", page.content()).group(1)
            sess = re.search(r"pIdSessao=([^&\"']+)", page.url).group(1)
            base = page.url.split("/pls/")[0] + "/pls/webhap/webNewCadastroUsuario."
            url = lambda tela: f"{base}{tela}?pPessoa={pessoa}&pNoCache={int(time.time() * 1000)}&pIdSessao={sess}"
            page.goto(url("Usuario"), timeout=600000, wait_until="domcontentloaded")   # 2M2DX/S7HNK: lista enorme
            cods = {re.sub(r"\D", "", c): k for c, k in
                    re.findall(r"(\d{3}\.\d{3}\.\d{3}-\d{2})\s+(?:\d+\s+)?([A-Z0-9]{5}\d{9})", txt(page))}
            for tipo, cpf, _nome, tit in acoes:
                if tipo not in ("CANCELA_CONTRATO", "CANCELA_DEP") or res.get(cpf, "").startswith("OK"):
                    continue
                ct = cods.get(cpf if tipo == "CANCELA_CONTRATO" else tit)
                if not ct:
                    res[cpf] = "SEM_CODIGO"
                    continue
                page.goto(url("CancelamentoContratoCodigo" if tipo == "CANCELA_CONTRATO" else "CancelamentoDependenteCodigo"))
                page.wait_for_load_state("networkidle")
                page.fill("input[name=pCodigoTitular]", ct)
                with page.expect_navigation(timeout=30000):
                    page.click("input[type=submit]")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(1500)
                t1 = txt(page)
                if "JÁ EFETUADO" in t1 or "JA EFETUADO" in t1:
                    res[cpf] = "OK JA_PEDIDO_ANTES"
                    continue
                if not page.query_selector("[name=pmotivocancelamento]"):
                    res[cpf] = "FALHA: " + dump(page, f"{cpf}_x")[:300]
                    continue
                page.select_option("[name=pmotivocancelamento]", "50")
                page.wait_for_timeout(500)
                if tipo == "CANCELA_CONTRATO":
                    page.evaluate("""() => { const s = document.getElementById('pmotivocancelamento'); if (typeof habilitaQuestionario == 'function') habilitaQuestionario(s);
                        const q = (n, v) => { const e = document.querySelector(`[name=${n}][value=${v}]`); if (e) e.checked = true; };
                        q('pFL_BENEF_MOT_EXCLUSAO', 'A'); q('pFL_BENEF_CONTINUA_EMPRESA', 'N'); q('pFL_BENEF_CONTRIBUIA_EMP', 'N');
                        const d = document.querySelector('[name=pDeclaracao]'); if (d) d.checked = true; }""")
                    with page.expect_navigation(timeout=60000):
                        page.click("input[type=submit][value=Prosseguir]")
                else:
                    dc = cods.get(cpf)
                    if not dc or not page.query_selector(f"[name=pCodigoDep][value=\"{dc}\"]"):
                        res[cpf] = "FALHA: dependente não listado"
                        continue
                    page.evaluate("v => { document.querySelector(`[name=pCodigoDep][value='${v}']`).checked = true; }", dc)
                    with page.expect_navigation(timeout=60000):
                        page.click("input[type=button][value=Efetivar]")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(2000)
                r = dump(page, f"{cpf}_2")
                res[cpf] = ("OK " if re.search(r"SUCESSO|EFETUAD|REGISTRAD", r) else "VER: ") + r[:400]
                _gravar(arq, res)
        finally:
            _gravar(arq, res)
            b.close()
    return {a[1]: res.get(a[1]) for a in acoes if a[1] in res}
