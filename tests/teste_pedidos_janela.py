"""22/11/2026 (varredura, MOV-25 l.1/2, pedido 64250137 de 20/05): a Hapvida recusa intervalo de mais de 180 dias
("O intervalo entre a data inicial e final não pode ultrapassar 180 dias") e o pedido mais velho sumia da conferência e
do inventário. A Cora lê em pedaços de 179 dias até 18 meses. Sem rede: a Hapvida é simulada com a mesma recusa."""
import sys
from datetime import date, timedelta
sys.path.insert(0, "/app")
import pedidos_situacao as ps  # noqa: E402

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg, flush=True)
    falhas += 0 if cond else 1


HOJE = date.today()
VELHO = HOJE - timedelta(days=186)          # como o 64250137 em 22/11 (digitado 20/05)
NOVO = HOJE - timedelta(days=10)
MOVS = [
    {"controlNumber": 64250137, "dependentControlNumber": 0, "cpfNumber": "19202999716", "operationDescription": "Inclusion",
     "processingStatus": 1, "processingStatusDescription": "Pendente", "typingDate": VELHO.strftime("%d/%m/%Y"), "_d": VELHO},
    {"controlNumber": 70100000, "dependentControlNumber": 0, "cpfNumber": "11111111111", "operationDescription": "Inclusion",
     "processingStatus": 1, "processingStatusDescription": "Pendente", "typingDate": NOVO.strftime("%d/%m/%Y"), "_d": NOVO},
]
chamadas = []


def falso_get(h, url, **p):
    ini, fim = date.fromisoformat(p["start-date"]), date.fromisoformat(p["end-date"])
    if (fim - ini).days > 180:
        raise RuntimeError("Hapvida respondeu 400: O intervalo entre a data inicial e final não pode ultrapassar 180 dias.")
    chamadas.append((ini, fim))
    dentro = [m for m in MOVS if ini <= m["_d"] <= fim]
    return {"movements": dentro[p["offset"]:p["offset"] + p["limit"]], "total": len(dentro)}


ps._get = falso_get
ps._sessao = lambda c: {}
ps._cadastro = lambda h, c, cpf: None
ps._docs = lambda h, cn: []

# inventário (madrugada): os 18 meses, nenhum pedaço acima de 180 dias, sem buraco nem sobreposição
chamadas.clear()
todos = ps._pedidos({}, "XEIJL")
ok(any(m["controlNumber"] == 64250137 for m in todos), "o pedido de 186 dias atrás aparece no inventário")
ok(all((f - i).days <= 180 for i, f in chamadas), "nenhum pedaço passa de 180 dias (a Hapvida recusaria)")
ok(chamadas[0][1] == HOJE and min(i for i, _ in chamadas) <= HOJE - timedelta(days=ps.HORIZONTE),
   "cobre de hoje até o horizonte")
ok(all(chamadas[k + 1][1] == chamadas[k][0] - timedelta(days=1) for k in range(len(chamadas) - 1)),
   "pedaços encostados: sem buraco e sem sobreposição")
ok(len(todos) == len({(m["controlNumber"], m["cpfNumber"]) for m in todos}), "nenhum pedido repetido")

# repetido na borda (a Hapvida devolve o mesmo movimento em dois pedaços): conta uma vez
orig = falso_get
ps._get = lambda h, url, **p: {"movements": MOVS[:1], "total": 1} if p["offset"] == 0 else {"movements": [], "total": 1}
ok(len(ps._pedidos({}, "XEIJL")) == 1, "o mesmo pedido em dois pedaços entra uma vez")
ps._get = orig

# conferência (de hora em hora): só volta no tempo se falta CPF
chamadas.clear()
r = ps.situacao("XEIJL", ["11111111111"])
ok(r["11111111111"].get("pedido") == "70100000" and len(chamadas) == 1, "todos achados no pedaço recente: uma consulta só")
chamadas.clear()
r = ps.situacao("XEIJL", ["19202999716"])
ok(r["19202999716"].get("pedido") == "64250137" and r["19202999716"].get("situacao") == "Pendente",
   "MOV-25: o pedido de maio volta a ser visto ao vivo (antes: nao_achou)")
ok(len(chamadas) == 2, "e para no pedaço em que achou")
chamadas.clear()
r = ps.situacao("XEIJL", ["99999999999"])
ok(r["99999999999"].get("nao_achou") is True, "quem não tem pedido em 18 meses segue nao_achou")

print(f"\n{falhas} falha(s)")
sys.exit(1 if falhas else 0)
