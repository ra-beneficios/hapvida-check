# Inclusão por formulário no Odonto Premium do Legado: titular + dependentes. Gerado por gerar_lote.py (famílias embutidas).
import re, sys, json, time
sys.path.insert(0, "/app")
import secrets_store, censo_legado
from playwright.sync_api import sync_playwright
import os
COD = "__COD__"; fams = json.loads(r"""__FAMS__"""); so = set(sys.argv[1:])
os.makedirs(f"/tmp/odo/{COD}/html", exist_ok=True)
cred = secrets_store.get_json("hapvida-legado")[COD]
OUT = f"/tmp/odo/{COD}/lote.json"
try: res = json.load(open(OUT))
except Exception: res = {}
alertas = []
txt = lambda t: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(style|script|select)[^>]*>.*?</\1>", " ", t)))
def setv(page, sel, v):
    if v: page.evaluate("([s, v]) => { const e = document.querySelector(s); if (!e.value) e.value = v; }", [sel, v])
def login(page):
    page.goto(censo_legado.LOGIN_URL, timeout=30000); page.wait_for_load_state("networkidle")
    page.fill("input[name='pCodigoEmpresa']", COD); page.keyboard.press("Tab")
    page.wait_for_selector("input[name='pSenha']", state="visible", timeout=10000)
    page.fill("input[name='pSenha']", cred["senha"]); page.click("#Prosseguir")
    page.wait_for_url(re.compile("pIdSessao="), timeout=30000); page.wait_for_timeout(2000)
    return page.url
def enviar(page):
    with page.expect_navigation(timeout=60000):
        page.click("#btProsseguir")
    page.wait_for_load_state("networkidle"); page.wait_for_timeout(2500)
def titular(page, menu, f):
    page.goto(menu); page.wait_for_load_state("networkidle")
    m = re.search(r"(webNewCadastroUsuario\.InclusaoContratoCpf\?[^'\"\s]+)", page.content())
    page.goto(page.url.split("/pls/")[0] + "/pls/webhap/" + m.group(1).replace("&amp;", "&")); page.wait_for_load_state("networkidle")
    page.fill("input[name=pCpfTitular]", f["cpf"])
    with page.expect_navigation(timeout=30000):
        page.evaluate("document.forms[0].submit()")
    page.wait_for_load_state("networkidle"); page.wait_for_timeout(3000)
    if "inclusaoContratoFicha" not in page.url or not page.query_selector("#pNomeTitular"):
        t = txt(page.content()); i = t.find("DATA:")
        return "BLOQUEADO: " + (t[i:i+300] if i >= 0 else t[-300:])
    setv(page, "#pNomeTitular", f["nome"][:45])
    # 017: a Receita abrevia o nome da mãe (ex.: "DULCELY DE B QUEIROZ"); com o nome completo da base, sobrescreve
    page.evaluate("""m => { const e = document.querySelector('[name=pMae]'); if (!e || !m) return;
        if (!e.value || / [A-Z]\.? /.test(' ' + e.value + ' ')) { e.removeAttribute('readonly'); e.value = m; } }""", (f.get("mae") or "")[:45])
    page.select_option("#pSexo", f["sexo"]); page.select_option("#pEstadoCivil", f["ec"])
    page.click("#pDataAdm2"); page.keyboard.type(f["adm"]); page.keyboard.press("Tab"); page.wait_for_timeout(2500)
    page.select_option("#pUnidade", "1"); page.wait_for_timeout(2500)
    page.select_option("#pPlano", "9732")
    page.click("#pCep"); page.keyboard.type(f["cep"]); page.keyboard.press("Tab"); page.wait_for_timeout(3000)
    page.select_option("#pLogradouro", f["tlog"])
    for k in ("pEndereco", "pNumero", "pCompl", "pBairro", "pCidade"):
        page.fill("#" + k, f[k][:60])
    page.select_option("#pUf", f["pUf"])
    page.evaluate("""() => { const o = document.querySelector('[name=pOrgao]'), u = document.querySelector('[name=pUfOrgao]');
        const m = (o && o.value || '').toUpperCase().match(/(AC|AL|AM|AP|BA|CE|DF|ES|GO|MA|MG|MS|MT|PA|PB|PE|PI|PR|RJ|RN|RO|RR|RS|SC|SE|SP|TO)$/);
        if (m && u && !u.value) u.value = m[1]; }""")
    page.evaluate("() => { const o = document.querySelector('[name=pOrgao]'), u = document.querySelector('[name=pUfOrgao]'); if (!o || !o.value || !u || !u.value) { document.querySelector('[name=pRg]').value = ''; o.value = ''; u.value = ''; } }")
    enviar(page)
    t = txt(page.content()); open(f"/tmp/odo/{COD}/html/tit_{f['cpf']}.html", "w").write(page.content())
    i = t.find("DATA:"); t = t[i:i+400] if i >= 0 else t[:400]
    if "TITULAR FOI CADASTRADO COM SUCESSO" in t: return "OK"
    if "PROCESSO DE VALIDA" in t: return "PENDENTE_DOC: " + re.search(r"CADASTRO EM PROCESSO[^.]*?(?= Hapvida Institucional|$)", t).group(0)
    return "FALHA: " + t
def dependente(page, d):
    if d["tipo"].startswith("?"): return "PULADO: parentesco " + d["tipo"]
    page.click("#pCpf"); page.keyboard.type(d["cpf"]); page.keyboard.press("Tab"); page.wait_for_timeout(3500)
    setv(page, "#pNomeDep", d["nome"][:45]); setv(page, "#pDataNasc", d["nasc"]); setv(page, "#pMae", d["mae"][:45])
    page.select_option("#pEstadoCivil", d["ec"]); page.select_option("#pTipoDep", d["tipo"]); page.select_option("#pSexo", d["sexo"])
    # 158: a ficha guarda a data de casamento do dependente anterior; sempre grava a deste (vazia se não for cônjuge)
    page.evaluate("v => { const e = document.querySelector('#pDtCasamento'); if (e) e.value = v; }", d["cas"] if d["tipo"] == "1" else "")
    page.evaluate("carregaVigenciaCargoDep(document.getElementById('pDataNasc'))"); page.wait_for_timeout(2000)
    n = len(alertas); enviar(page)
    t = txt(page.content()); open(f"/tmp/odo/{COD}/html/dep_{d['cpf']}.html", "w").write(page.content())
    m = re.search(r"\*\s*\*\s*\*([^*]{5,200})\*\s*\*\s*\*", t)
    return (m.group(1).strip() if m else t[-300:]) + (" | ALERTAS: " + "; ".join(alertas[n:]) if alertas[n:] else "")
with sync_playwright() as p:
    b = p.chromium.launch(headless=True); page = b.new_page()
    page.on("dialog", lambda dg: (alertas.append(dg.message), dg.accept()))
    menu = login(page)
    for f in fams:
        if so and f["cpf"] not in so: continue
        if f["cpf"] in res and not res[f["cpf"]]["titular"].startswith("FALHA"): continue  # FALHA = titular não gravou; pode tentar de novo
        r = {"titular": titular(page, menu, f), "deps": {}}
        print(f["cpf"], f["nome"], "->", r["titular"][:200], flush=True)
        if r["titular"] == "OK":
            r["controle"] = page.evaluate("document.querySelector('input[name=pControle]')?.value")
            for d in f["deps"]:
                r["deps"][d["cpf"]] = dependente(page, d)
                print("   dep", d["cpf"], d["nome"], "->", r["deps"][d["cpf"]][:250], flush=True)
                if "inclusaoDependente" not in page.url.lower() and "InclusaoDependente" not in page.url:
                    print("   saiu da ficha de dependente:", page.url.split("?")[0]); break
        res[f["cpf"]] = r; json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1)
    b.close()
