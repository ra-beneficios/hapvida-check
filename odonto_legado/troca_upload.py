# Envia UM arquivo pela Troca de Arquivos (upload) do contrato. Conteúdo vem em base64 na env ARQ_B64.
import sys, re, os, base64
sys.path.insert(0, "/app")
import secrets_store, legado_auto
from playwright.sync_api import sync_playwright
cod, nome = sys.argv[1], sys.argv[2]; c = secrets_store.get_json("hapvida-legado")[cod]
dados = base64.b64decode(os.environ["ARQ_B64"])
with sync_playwright() as p:
    b = p.chromium.launch(headless=True); page = b.new_page()
    page.on("dialog", lambda d: (print("DIALOG", d.message[:300]), d.accept()))
    legado_auto._login(page, "webNewTrocaArquivo", [("input[name=pCpf]", c["cnpj"].zfill(14)), ("input[name=pSenha]", c.get("senha_troca") or c["senha"])])
    page.click("text=ENVIAR ARQUIVOS - UPLOAD"); page.wait_for_load_state("networkidle")
    page.set_input_files("input[name=pUpload]", {"name": nome, "mimeType": "text/plain", "buffer": dados})
    with page.expect_navigation(timeout=120000):
        page.click("input[type=submit]")
    page.wait_for_load_state("networkidle"); page.wait_for_timeout(2000)
    os.makedirs(f"/tmp/odo/{cod}/troca", exist_ok=True); open(f"/tmp/odo/{cod}/troca/upload_res.html", "w").write(page.content())
    t = re.sub(r"\s+", " ", page.locator("body").inner_text()); i = t.find("Upload"); print("RES", t[i:i+600])
    b.close()
