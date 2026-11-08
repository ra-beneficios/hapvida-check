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


class SessaoLegado(Exception):
    """Sem sessão válida do Legado para este código (captcha no login do robô ou sessão colada vencida/de outro código)."""


LEGADO = "https://www.hapvida.com.br/pls/webhap/webNewCadastroUsuario."


def conferir_sessao(sessao: dict) -> str:
    """04/11/2026 (André: o login da Movimentação tem reCAPTCHA v3 e recusa o robô): sessão que o André abriu e colou na
    tela. Só leitura: abre o menu e devolve o código da empresa ("CÓDIGO: S7HNK"). SessaoLegado se vencida."""
    import httpx
    p, s = str(sessao.get("pPessoa") or ""), str(sessao.get("pIdSessao") or "")
    if not (re.fullmatch(r"\d{5,12}", p) and re.fullmatch(r"\d{10,30}", s)):
        raise SessaoLegado("a URL não tem pPessoa e pIdSessao")
    r = httpx.get(f"{LEGADO}SistemaMenu?pPessoa={p}&pNoCache={int(time.time() * 1000)}&pIdSessao={s}", timeout=60)
    # o menu traz "<li><b>Código: </b>2M2DX</li>" (04/11/2026)
    m = re.search(r"C[ÓOóo]DIGO:\s*(?:<[^>]+>\s*)*([0-9A-Z]{5})\b", r.text, re.I)
    if r.status_code != 200 or "InclusaoContratoCpf" not in r.text or not m:
        raise SessaoLegado("a sessão do Legado venceu ou não abriu o menu — faça o login de novo e cole a URL")
    return m.group(1)


def _login(page, cod: str, sessao: dict | None = None) -> str:
    if sessao:
        # 04/11/2026: sessão colada pelo André (o robô não passa no captcha). Confere que é DESTE código
        if conferir_sessao(sessao) != cod:
            raise SessaoLegado(f"a sessão colada não é do contrato {cod}")
        page.goto(f"{LEGADO}SistemaMenu?pPessoa={sessao['pPessoa']}&pNoCache={int(time.time() * 1000)}"
                  f"&pIdSessao={sessao['pIdSessao']}", timeout=60000)
        page.wait_for_load_state("networkidle")
        return page.url
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
    # 03/11/2026 (legado_captcha_premium): a URL de recusa também tem pIdSessao= — antes "entrava" e quebrava com 500
    if "CAPTCHA" in page.content().upper() or "InclusaoContratoCpf" not in page.content():
        raise SessaoLegado(f"o login do Legado ({cod}) recusou o robô (captcha): precisa da sessão colada na tela")
    return page.url


# 06/11/2026 (André, MOV-000014 l.11 HELLOÁ: "a melhor regra porque não falhamos"): o Premium vai com o cadastro que a
# Saúde ACEITOU (é o dado que a Hapvida já validou), não com o do layout nem com o que o Legado preenche sozinho pelo CPF.
# Em 06/11, 75 de 339 vidas do Premium iam com nome/mãe/nascimento/sexo diferentes da Saúde (3 caíram em "validação de
# documento"). RG/órgão/UF a Saúde não tem: vão em branco (nada que a Hapvida não validou).
def cadastro_saude(contrato: str, cpfs: set[str]) -> dict[str, dict]:
    """{cpf: {nome, nasc dd/mm/aaaa, mae, sexo M|F}} do cadastro ATIVO no contrato de Saúde (portal moderno, só leitura).
    CPF sem cadastro ativo (subiu limpo e ainda não ativou) fica de fora: vai o layout, que é o que a Saúde recebeu."""
    import sys
    sys.path.insert(0, os.environ.get("SANEAMENTO_PATH", "/lifetools_service"))
    from saneamento import coleta_portal
    out = {}
    with coleta_portal.sessao(contrato) as (page, headers):
        for cpf in sorted(cpfs):
            for c in coleta_portal.certificados_do_cpf(page, headers, contrato, cpf) or []:
                st = ((c.get("statusDetail") or {}).get("description") or c.get("_status_lista") or "").strip().upper()
                if st != "ATIVO":
                    continue
                nm = c.get("name") if isinstance(c.get("name"), dict) else {"full": c.get("name")}
                m = re.match(r"(\d{4})-(\d{2})-(\d{2})|(\d{2})/(\d{2})/(\d{4})", (c.get("birthDate") or "").strip())
                nasc = (f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m.group(1) else m.group(0)[:10]) if m else ""
                g = c.get("gender")
                g = ((g.get("type") if isinstance(g, dict) else g) or "").strip().upper()[:1]
                out[cpf] = {k: v for k, v in (("nome", (nm.get("full") or "").strip()), ("mae", (nm.get("mom") or "").strip()),
                                               ("nasc", nasc), ("sexo", g if g in ("M", "F") else "")) if v}
                break
    return out


def aplicar_cadastro_saude(familias: list[dict], cad: dict[str, dict]) -> list[dict]:
    """Troca nome, mãe, nascimento e sexo de cada pessoa pelo cadastro ativo da Saúde. Marca quem foi trocado ("_saude")."""
    for f in familias:
        for pessoa, campos in [(f, ("nome", "mae", "sexo"))] + [(d, ("nome", "mae", "nasc", "sexo")) for d in f.get("deps") or []]:
            c = cad.get(pessoa.get("cpf")) or {}
            mud = {k: c[k] for k in campos if c.get(k) and c[k] != pessoa.get(k)}
            pessoa.update(mud)
            pessoa["_saude"] = bool(c)
            if mud:
                pessoa["_trocado"] = sorted(mud)
    return familias


def _sem_rg(page):
    """RG, órgão emissor e UF que o Legado preenche pelo CPF vão em branco (a Saúde não tem; 06/11/2026)."""
    page.evaluate("() => ['pRg', 'pOrgao', 'pUfOrgao'].forEach(n => document.querySelectorAll('[name=' + n + ']')"
                  ".forEach(e => { e.removeAttribute('readonly'); e.value = ''; }))")


def _ficha(page, html: Path, nome: str):
    """A ficha como vai (campos preenchidos), guardada antes de enviar: prova do que foi se o Legado pedir documento."""
    try:
        v = page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('input,select,textarea')]
            .filter(e => e.name && e.type !== 'hidden' && e.value).map(e => [e.name, e.value]))""")
        (html / f"ficha_{nome}.json").write_text(json.dumps(v, ensure_ascii=False, indent=1))
    except Exception:
        pass


def _forca(page, sel, v):
    """Grava o valor mesmo se o Legado já preencheu (o cadastro da Saúde manda)."""
    if v:
        page.evaluate("([s, v]) => { const e = document.querySelector(s); if (e) { e.removeAttribute('readonly'); e.value = v; } }",
                      [sel, v])


def _setv(page, sel, v):
    if v:
        page.evaluate("([s, v]) => { const e = document.querySelector(s); if (!e.value) e.value = v; }", [sel, v])


def _espera_neoway(page, acao) -> dict | None:
    """08/11/2026 (auditoria, MOV-000014 l.11 HELLOÁ): ao sair do CPF o Legado consulta a base dele
    (AjaxRetornaDadosCpfNeoway) e PREENCHE nome/nascimento/mãe — é por isso que o manual não trava. O robô esperava 3,5 s
    fixos: com a consulta lenta o campo ainda estava vazio, o robô gravava o dado do layout (Helloá 05/09/2017; a Hapvida
    tem 05/09/2014) e o Legado devolvia "346-divergências de dados cadastrais". Agora espera a resposta (até 20 s) e o
    preenchimento antes de seguir; o que o Legado preencheu fica (só o cadastro ATIVO da Saúde sobrescreve, regra 06/11)."""
    try:
        with page.expect_response(lambda r: "AjaxRetornaDadosCpfNeoway" in r.url, timeout=20000) as resp:
            acao()
        dados = (resp.value.json() or {}).get("cpf") or []
        page.wait_for_timeout(800)          # o JS do Legado grava os campos depois da resposta
        return dados[0] if dados else None
    except Exception:
        page.wait_for_timeout(3500)         # sem a consulta (CPF sem cadastro, tela antiga): como era
        return None


def _enviar(page):
    # 04/11/2026 (S7HNK, CLEISSON 30/10: "020-Obrigatorio informar nu identidade quando informa orgao emissor/uf"):
    # órgão emissor/UF sem o número do RG -> vão os três em branco
    page.evaluate("() => { const r = document.querySelector('[name=pRg]'), o = document.querySelector('[name=pOrgao]'),"
                  " u = document.querySelector('[name=pUfOrgao]'); if (r && !r.value.trim()) { if (o) o.value = '';"
                  " if (u) u.value = ''; } }")
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
    try:    # 08/11/2026: a ficha do titular também se preenche pela base da Hapvida — espera (ver _espera_neoway)
        page.wait_for_function("() => (document.querySelector('#pNomeTitular') || {}).value", timeout=15000)
    except Exception:
        pass
    (_forca if f.get("_saude") else _setv)(page, "#pNomeTitular", f["nome"][:45])
    # 017: a Receita abrevia o nome da mãe (ex.: "DULCELY DE B QUEIROZ"); com o nome completo, sobrescreve
    page.evaluate("""m => { const e = document.querySelector('[name=pMae]'); if (!e || !m) return;
        if (!e.value || / [A-Z]\\.? /.test(' ' + e.value + ' ')) { e.removeAttribute('readonly'); e.value = m; } }""",
                  (f.get("mae") or "")[:45])
    if f.get("_saude"):
        _forca(page, "[name=pMae]", (f.get("mae") or "")[:45])
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
    _sem_rg(page)
    _ficha(page, html, f"tit_{f['cpf']}")
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
    _espera_neoway(page, lambda: page.keyboard.press("Tab"))
    preenche = _forca if d.get("_saude") else _setv
    preenche(page, "#pNomeDep", d["nome"][:45])
    preenche(page, "#pDataNasc", d["nasc"])
    preenche(page, "#pMae", d["mae"][:45])
    page.select_option("#pEstadoCivil", d["ec"])
    page.select_option("#pTipoDep", d["tipo"])
    page.select_option("#pSexo", d["sexo"])
    # 158: a ficha guarda a data de casamento do dependente anterior; sempre grava a deste (vazia se não for cônjuge)
    page.evaluate("v => { const e = document.querySelector('#pDtCasamento'); if (e) e.value = v; }",
                  d["cas"] if d["tipo"] == "1" else "")
    page.evaluate("carregaVigenciaCargoDep(document.getElementById('pDataNasc'))")
    page.wait_for_timeout(2000)
    # 05/11/2026 (S7HNK, SUELLEN, MOV-000012): na tela "Inclusão de Novos Dependentes" (titular já ativo) o plano vem
    # num select que o JS carrega depois da data; sem ele o Legado devolve "094-Plano do dependente deve ser informado"
    if page.query_selector("select[name=pTipoPlano]"):
        for _ in range(20):
            opts = page.eval_on_selector_all("select[name=pTipoPlano] option", "os => os.map(o => [o.value, o.text])")
            if any(v for v, _ in opts):
                break
            page.wait_for_timeout(1000)
        plano = next((v for v, t in opts if v), None)
        if plano:
            page.select_option("select[name=pTipoPlano]", plano)
            page.wait_for_timeout(1500)
    n = len(alertas)
    _sem_rg(page)
    _ficha(page, html, f"dep_{d['cpf']}")
    _enviar(page)
    (html / f"dep_{d['cpf']}.html").write_text(page.content())
    t = _txt(page.content())
    m = re.search(r"\*\s*\*\s*\*([^*]{5,200})\*\s*\*\s*\*", t)
    # 05/11/2026 (2M2DX, MOV-000014 l.11/113/119): "CADASTRO EM PROCESSO DE VALIDAÇÃO DE DADOS, ANEXAR DOCUMENTAÇÃO..."
    # vem sem os asteriscos — antes o rodapé da página virava o motivo e a vida aparecia recusada
    v = re.search(r"CADASTRO EM PROCESSO DE VALIDA[ÇC][ÃA]O[^.]{0,160}", t)
    if not m and v:
        return "PENDENTE_DOC " + v.group(0).strip() + (" | ALERTAS: " + "; ".join(alertas[n:]) if alertas[n:] else "")
    return (m.group(1).strip() if m else t[-300:]) + (" | ALERTAS: " + "; ".join(alertas[n:]) if alertas[n:] else "")


def _ficha_novo_dependente(page, menu: str, cpf_titular: str) -> bool:
    """04/11/2026 (S7HNK, MOV-000012): abre o pedido já digitado do titular (Alterar Pedido) e vai para "Novo Dependente".
    Serve quando o titular já tem pedido ("JÁ EXISTE UM PEDIDO DIGITADO PARA ESTE CPF") e quando, depois de um dependente,
    a tela não volta para a ficha. -> True se a ficha de dependente abriu."""
    base = page.url.split("/pls/")[0] + "/pls/webhap/webNewCadastroUsuario."
    q = menu.split("?", 1)[1] if "?" in menu else ""
    page.goto(f"{base}AlteracaoContratoCpf?{q}")
    page.wait_for_load_state("networkidle")
    page.fill("input[name=pCpfTitular]", cpf_titular)
    with page.expect_navigation(timeout=30000):
        page.evaluate("document.forms[0].submit()")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2000)
    if "AlteracaoContratoFicha" not in page.url:
        return False
    with page.expect_navigation(timeout=60000):
        page.click("input[type=submit][value='Novo Dependente']")
    page.wait_for_load_state("networkidle")
    try:
        page.wait_for_selector("#pCpf", state="visible", timeout=30000)
        return True
    except Exception:
        return False


def incluir(cod: str, familias: list[dict], sessao: dict | None = None, saude: str | None = None) -> dict:
    """Formulário do Legado: titular + dependentes, família por família. -> {cpf_titular: {titular, deps: {cpf: msg}}}
    saude: código do contrato de Saúde — nome/mãe/nascimento/sexo vêm do cadastro ativo lá (06/11/2026)."""
    from playwright.sync_api import sync_playwright
    if saude:
        cpfs = {f["cpf"] for f in familias} | {d["cpf"] for f in familias for d in f.get("deps") or []}
        try:
            cad = cadastro_saude(saude, cpfs)
        except Exception as e:      # sem a consulta, vai o layout (o que a Saúde recebeu limpo) — como era até 05/11
            print(f"[odonto_premium] {cod}: cadastro da Saúde {saude} indisponível ({type(e).__name__}: {e}); vai o layout")
            cad = {}
        aplicar_cadastro_saude(familias, cad)
        trocas = [(x["cpf"], x["_trocado"]) for f in familias for x in [f] + (f.get("deps") or []) if x.get("_trocado")]
        print(f"[odonto_premium] {cod}: {len(cad)} de {len(cpfs)} com cadastro ativo na Saúde {saude}; trocados: {trocas}")
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
            menu = _login(page, cod, sessao)
            for f in familias:
                ja = (res.get(f["cpf"]) or {}).get("titular") or ""
                if ja == "OK" or ja.startswith("PENDENTE_DOC"):
                    continue        # já foi: não cadastra de novo (FALHA/BLOQUEADO tenta de novo — 04/11: "pedido digitado")
                r = {"titular": _titular(page, menu, f, html), "deps": {}}
                if "JÁ EXISTE UM PEDIDO DIGITADO" in r["titular"].upper():
                    # 04/11/2026: o titular já está no Legado (pedido digitado antes): os dependentes entram no pedido dele
                    r["titular"] = "OK"
                    r["ja_digitado"] = True
                if r["titular"] == "OK" or r["titular"].startswith("PENDENTE_DOC"):
                    # 28/10/2026 (UTC SY8MI): o "em validação" também já tem controle (é por ele que o documento entra)
                    r["controle"] = page.evaluate("document.querySelector('input[name=pControle]')?.value")
                if r["titular"] == "OK":
                    for k, d in enumerate(f["deps"]):
                        # 04/11/2026: a ficha de dependente nem sempre volta depois de um cadastro (e o pedido já digitado
                        # começa fora dela): reabre pelo Alterar Pedido > Novo Dependente antes de cada um que precisar
                        if (r.get("ja_digitado") and k == 0) or not page.query_selector("#pCpf"):
                            if not _ficha_novo_dependente(page, menu, f["cpf"]):
                                break   # não abriu: os que faltam ficam sem resposta (voltam como recusados)
                        r["deps"][d["cpf"]] = _dependente(page, d, html, alertas)
                res[f["cpf"]] = r
                _gravar(arq, res)
        finally:
            b.close()
    return {f["cpf"]: res.get(f["cpf"]) for f in familias if f["cpf"] in res}


def pre_cancelar(cod: str, acoes: list[list], sessao: dict | None = None) -> dict:
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
            _login(page, cod, sessao)
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
