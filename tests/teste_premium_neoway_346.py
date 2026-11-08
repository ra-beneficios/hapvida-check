"""08/11/2026 (André, pendência 346 no Premium): mãe e nascimento que o Legado preenche pelo CPF (base certificada,
Neoway) ficam; o nosso dado só entra no campo vazio ou na mãe abreviada (017). A ficha do dependente é limpa antes do CPF
(guardava o dependente anterior). Caso real: Helloá foi com 05/09/2017, a Hapvida tem 05/09/2014."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import odonto_premium as op  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

FICHA = """<input id=pNomeDep value='HELLOA CRISTINA PEREIRA CERQUEIRA'><input id=pDataNasc value='05/09/2014'>
<input id=pMae value='KELLY PEREIRA FRANCISCO'><input name=pMae id=pMaeTit value='DULCELY DE B QUEIROZ'>"""

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok   " if cond else "FALHA ") + msg)
    falhas += not cond


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    page = b.new_page()
    page.set_content(FICHA)
    v = lambda s: page.eval_on_selector(s, "e => e.value")
    op._setv(page, "#pDataNasc", "05/09/2017")
    ok(v("#pDataNasc") == "05/09/2014", "nascimento do Legado fica (não grava 2017 por cima)")
    op._mae_completa(page, "#pMae", "KELLY P FRANCISCO DA SILVA")
    ok(v("#pMae") == "KELLY PEREIRA FRANCISCO", "mãe completa do Legado fica")
    op._mae_completa(page, "[name=pMae]", "DULCELY DE BARROS QUEIROZ")
    ok(v("#pMaeTit") == "DULCELY DE BARROS QUEIROZ", "mãe abreviada do Legado é completada (017)")
    op._limpar(page, ("#pNomeDep", "#pDataNasc", "#pMae"))
    ok(v("#pNomeDep") == v("#pDataNasc") == v("#pMae") == "", "ficha limpa antes do CPF do próximo dependente")
    op._setv(page, "#pDataNasc", "05/09/2017")
    op._mae_completa(page, "#pMae", "KELLY PEREIRA FRANCISCO")
    ok(v("#pDataNasc") == "05/09/2017" and v("#pMae") == "KELLY PEREIRA FRANCISCO", "Legado sem dado: vai o nosso")
    b.close()

print(f"\n{falhas} falha(s)")
sys.exit(1 if falhas else 0)
