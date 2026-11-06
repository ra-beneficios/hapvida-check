"""06/11/2026 (André, MOV-000014 l.11 HELLOÁ): o Odonto Premium vai com o cadastro ATIVO da Saúde, não com o layout. Sem rede."""
import sys
import types
from contextlib import contextmanager

sys.path.insert(0, "/app")
import odonto_premium as op  # noqa: E402

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg, flush=True)
    falhas += 0 if cond else 1


CAD = {"22625367750": [{"name": {"full": "HELLOA CRISTINA PEREIRA CERQUEIRA", "mom": "KELLY PEREIRA FRANCISCO"},
                        "birthDate": "05/09/2017", "gender": {"type": "F"}, "statusDetail": {"description": "Ativo"}}],
       "02844606563": [{"name": {"full": "PAULO HENRIQUE DE JESUS CERQUEIRA", "mom": "GENOLINA DE JESUS"},
                        "birthDate": "1983-12-04", "gender": {"type": "M"}, "_status_lista": "Cancelado"}]}


@contextmanager
def sessao(ct):
    yield None, {}


falso = types.SimpleNamespace(sessao=sessao, certificados_do_cpf=lambda p, h, ct, cpf: CAD.get(cpf, []))
sys.modules["saneamento"] = types.SimpleNamespace(coleta_portal=falso)
sys.modules["saneamento.coleta_portal"] = falso
cad = op.cadastro_saude("4DFEV", {"22625367750", "02844606563", "15981882751"})
ok(cad == {"22625367750": {"nome": "HELLOA CRISTINA PEREIRA CERQUEIRA", "mae": "KELLY PEREIRA FRANCISCO",
                           "nasc": "05/09/2017", "sexo": "F"}}, f"só o cadastro ATIVO vale ({cad})")

fam = [{"cpf": "02844606563", "nome": "PAULO", "mae": "GENOLINA DE JESUS", "sexo": "M",
        "deps": [{"cpf": "22625367750", "nome": "HELLOÁ CRISTINA PEREIRA CERQUEIRA", "nasc": "05/09/2014", "sexo": "M",
                  "mae": "KELLY PEREIRA FRANCISCO"},
                 {"cpf": "15981882751", "nome": "JULIA GOMES SILVA", "nasc": "26/04/2010", "sexo": "F", "mae": "X"}]}]
op.aplicar_cadastro_saude(fam, cad)
h, j = fam[0]["deps"]
ok(h["nasc"] == "05/09/2017" and h["sexo"] == "F" and h["nome"] == "HELLOA CRISTINA PEREIRA CERQUEIRA" and h["_saude"],
   f"Helloá vai como na Saúde ({h})")
ok(j["nasc"] == "26/04/2010" and not j["_saude"], "sem cadastro ativo (ainda não ativou): vai o layout")
ok(fam[0]["nome"] == "PAULO" and not fam[0]["_saude"], "titular com cadastro cancelado: vai o layout")
print({"falhas": falhas})
sys.exit(1 if falhas else 0)
