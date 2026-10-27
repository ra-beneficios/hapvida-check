"""SÓ LEITURA no Legado (webNewCadastroUsuario): por código, baixa Críticas Cadastrais (CriticaCadastral -> Rel, até 90 dias),
Inclusões Pendentes (pr_relPedidoPendente) e a lista de ativos (Usuario). Saída /dados_motor/_legado_pendencias/<COD>/."""
import json, os, re, sys, time
os.umask(0o077)
sys.path.insert(0, "/app")
import secrets_store, censo_legado
from playwright.sync_api import sync_playwright
INI, FIM = os.environ.get("INI", "01/09/2026"), os.environ.get("FIM", time.strftime("%d/%m/%Y"))
BASE = "/dados_motor/_legado_pendencias"
creds = secrets_store.get_json("hapvida-legado")
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    for COD in sys.argv[1:]:
        out = f"{BASE}/{COD}"; os.makedirs(out, mode=0o700, exist_ok=True)
        ctx = b.new_context(accept_downloads=True); page = ctx.new_page()
        try:
            page.goto(censo_legado.LOGIN_URL, timeout=30000); page.wait_for_load_state("networkidle")
            page.fill("input[name='pCodigoEmpresa']", COD); page.keyboard.press("Tab")
            page.wait_for_selector("input[name='pSenha']", state="visible", timeout=10000)
            page.fill("input[name='pSenha']", creds[COD]["senha"]); page.click("#Prosseguir")
            page.wait_for_url(re.compile("pIdSessao="), timeout=30000); page.wait_for_timeout(2500)
            pessoa = re.search(r"pPessoa=(\d+)", page.content()).group(1); sess = re.search(r"pIdSessao=([^&\"']+)", page.url).group(1)
            base = page.url.split("/pls/")[0] + "/pls/webhap/webNewCadastroUsuario."
            url = lambda t: f"{base}{t}?pPessoa={pessoa}&pNoCache={int(time.time()*1000)}&pIdSessao={sess}"
            res = {}
            for tela, nome in (("CriticaCadastral", "criticas"), ("pr_relPedidoPendente", "pendentes")):
                page.goto(url(tela), timeout=120000); page.wait_for_load_state("networkidle")
                page.fill("#pdt_inicio", INI); page.fill("#pdt_fim", FIM)
                try:
                    with page.expect_download(timeout=180000) as d:
                        page.click("input[type=submit]")
                    arq = f"{out}/{nome}_{d.value.suggested_filename}"; d.value.save_as(arq); res[nome] = arq
                except Exception as e:   # sem dado o portal responde com página, não arquivo
                    page.wait_for_timeout(1500)
                    t = re.sub(r"\s+", " ", page.locator("body").inner_text())
                    i = t.find("Sistema de Movimen"); res[nome] = "PAGINA: " + t[i:i+300]
                    open(f"{out}/{nome}.html", "w").write(page.content())
            page.goto(url("Usuario"), timeout=600000, wait_until="domcontentloaded")
            open(f"{out}/usuario.html", "w").write(page.content()); res["usuario"] = "ok"
            print(COD, json.dumps(res, ensure_ascii=False)[:600], flush=True)
        except Exception as e:
            print(COD, "ERRO", type(e).__name__, str(e)[:200], flush=True)
        finally:
            ctx.close(); time.sleep(2)
    b.close()
