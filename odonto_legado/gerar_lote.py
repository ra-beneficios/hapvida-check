# Gera lote_<COD>.py com as famílias embutidas, sem quem já tem pedido pendente no Legado.
import sys, json, os
AQUI = os.path.dirname(os.path.abspath(__file__))
cod = sys.argv[1]; arq = sys.argv[2] if len(sys.argv) > 2 else f"fam_{cod}.json"
pend = set(json.load(open("pendentes.json")).get(cod, [])) if os.path.exists("pendentes.json") else set()
fams = [f for f in json.load(open(arq)) if f["cpf"] not in pend]
s = json.dumps(fams, ensure_ascii=False); assert '"""' not in s
open(f"lote_{cod}.py", "w").write(open(os.path.join(AQUI, "lote_formulario_tpl.py")).read().replace("__COD__", cod).replace("__FAMS__", s))
print(cod, len(fams), "familias")
