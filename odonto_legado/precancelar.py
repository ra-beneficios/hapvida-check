# Pré-cancelamento no Legado (Odonto Premium). MODO=ver para na tela do motivo (não confirma); MODO=efetivar confirma.
import re, sys, os, time, json
sys.path.insert(0, "/app")
import secrets_store, censo_legado
from playwright.sync_api import sync_playwright
COD = sys.argv[1]; MODO = sys.argv[2]; acoes = json.loads(os.environ["ACOES"]); transf = json.loads(os.environ.get("TRANSF", "{}"))
cred = secrets_store.get_json("hapvida-legado")[COD]; out = f"/tmp/odo/{COD}/excl"; os.makedirs(out, exist_ok=True)
OUT = f"{out}/res.json"
try: res = json.load(open(OUT))
except Exception: res = {}
txt = lambda page: re.sub(r"\s+", " ", page.locator("body").inner_text())
def dump(page, tag):
    open(f"{out}/{tag}.html", "w").write(page.content())
    t = txt(page); i = t.find("DATA:"); return t[i:i+700]
with sync_playwright() as p:
    b = p.chromium.launch(headless=True); page = b.new_page()
    page.on("dialog", lambda d: (print("  DIALOG", d.message[:200]), d.accept()))
    page.goto(censo_legado.LOGIN_URL, timeout=30000); page.wait_for_load_state("networkidle")
    page.fill("input[name='pCodigoEmpresa']", COD); page.keyboard.press("Tab")
    page.wait_for_selector("input[name='pSenha']", state="visible", timeout=10000)
    page.fill("input[name='pSenha']", cred["senha"]); page.click("#Prosseguir")
    page.wait_for_url(re.compile("pIdSessao="), timeout=30000); page.wait_for_timeout(2500)
    pessoa = re.search(r"pPessoa=(\d+)", page.content()).group(1); sess = re.search(r"pIdSessao=([^&\"']+)", page.url).group(1)
    base = page.url.split("/pls/")[0] + "/pls/webhap/webNewCadastroUsuario."
    url = lambda tela: f"{base}{tela}?pPessoa={pessoa}&pNoCache={int(time.time()*1000)}&pIdSessao={sess}"
    page.goto(url("Usuario"), timeout=600000, wait_until="domcontentloaded")
    cods = {re.sub(r"\D", "", c): k for c, k in re.findall(r"(\d{3}\.\d{3}\.\d{3}-\d{2})\s+(?:\d+\s+)?([A-Z0-9]{5}\d{9})", txt(page))}
    print("codigos", len(cods))
    for tipo, cpf, nome, tit in acoes:
        if tipo not in ("CANCELA_CONTRATO", "CANCELA_DEP"): continue
        if res.get(cpf, "").startswith("OK"): continue
        titcpf = cpf if tipo == "CANCELA_CONTRATO" else tit
        ct = cods.get(titcpf)
        if not ct: res[cpf] = "SEM_CODIGO"; print(cpf, nome, "SEM_CODIGO"); continue
        tela = "CancelamentoContratoCodigo" if tipo == "CANCELA_CONTRATO" else "CancelamentoDependenteCodigo"
        page.goto(url(tela)); page.wait_for_load_state("networkidle")
        page.fill("input[name=pCodigoTitular]", ct)
        with page.expect_navigation(timeout=30000):
            page.click("input[type=submit]")
        page.wait_for_load_state("networkidle"); page.wait_for_timeout(1500)
        print("==", tipo, cpf, nome, ct, "|", dump(page, f"{cpf}_1")[:500])
        for f in page.locator("input,select").all():
            try: print("  CAMPO", f.evaluate("e => [e.tagName, e.type, e.name, e.id, e.type=='hidden'?'':e.value].join(' ')")[:120])
            except Exception: pass
        for o in page.locator("select option").all(): print("  OPT", o.get_attribute("value"), o.inner_text().strip()[:70])
        if MODO == "ver": continue
        t1 = txt(page)
        if "JÁ EFETUADO" in t1 or "JA EFETUADO" in t1:
            res[cpf] = "OK JA_PEDIDO_ANTES"; print("   ->", res[cpf]); continue
        if not page.query_selector("[name=pmotivocancelamento]"):
            res[cpf] = "FALHA: " + dump(page, f"{cpf}_x")[:300]; print("   ->", res[cpf]); continue
        page.select_option("[name=pmotivocancelamento]", "50"); page.wait_for_timeout(500)
        if tipo == "CANCELA_CONTRATO":
            page.evaluate("""() => { const s = document.getElementById('pmotivocancelamento'); if (typeof habilitaQuestionario == 'function') habilitaQuestionario(s);
                const q = (n, v) => { const e = document.querySelector(`[name=${n}][value=${v}]`); if (e) e.checked = true; };
                q('pFL_BENEF_MOT_EXCLUSAO', 'A'); q('pFL_BENEF_CONTINUA_EMPRESA', 'N'); q('pFL_BENEF_CONTRIBUIA_EMP', 'N');
                const d = document.querySelector('[name=pDeclaracao]'); if (d) d.checked = true; }""")
            print("   visivel:", page.evaluate("() => getComputedStyle(document.getElementById('Questionario')).display"))
            with page.expect_navigation(timeout=60000):
                page.click("input[type=submit][value=Prosseguir]")
        else:
            dc = cods.get(cpf)
            if not dc or not page.query_selector(f"[name=pCodigoDep][value=\"{dc}\"]"):
                res[cpf] = "FALHA: dependente não listado"; print("   ->", res[cpf]); continue
            page.evaluate("v => { document.querySelector(`[name=pCodigoDep][value='${v}']`).checked = true; }", dc)
            with page.expect_navigation(timeout=60000):
                page.click("input[type=button][value=Efetivar]")
        page.wait_for_load_state("networkidle"); page.wait_for_timeout(2000)
        r = dump(page, f"{cpf}_2"); res[cpf] = ("OK " if re.search(r"SUCESSO|EFETUAD|REGISTRAD", r) else "VER: ") + r[:400]
        print("   ->", res[cpf][:400]); json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1)
    json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=1)
    b.close()
