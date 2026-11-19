"""19/11/2026 (André: "a validação tem que estar no robô"): o cadastro de cada CPF no contrato (tela Beneficiários).
Resposta real de FHMWR 19/11 (Gabryella, MOV-45 l.6). Sem rede: _get simulado."""
import sys
sys.path.insert(0, "/app")
import pedidos_situacao as ps  # noqa: E402

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg, flush=True)
    falhas += 0 if cond else 1


REAL = {"beneficiaries": [{"beneficiary": {"status": "Ativo", "code": "FHMWR000747025", "type": "Dependente",
                                           "contractDetail": {"partnerCompany": "FHMWR"},
                                           "user": {"name": "GABRYELLA DA COSTA LIMA ARAGAO", "document": "21209133741"}}},
                          {"beneficiary": {"status": "Ativo", "code": "FHMWR000747009", "type": "Titular",
                                           "contractDetail": {"partnerCompany": "FHMWR"},
                                           "user": {"name": "OUTRA PESSOA", "document": "11095702793"}}}],
        "total": 2}
chamadas = []


def _get(h, url, **params):
    chamadas.append((url, params))
    if params.get("search") == "00000000191":
        raise RuntimeError("Hapvida respondeu 500")
    return REAL if params.get("search") == "21209133741" else {"beneficiaries": [], "total": 0}


ps._get = _get
ps._sessao = lambda c: {}
r = ps._cadastro({}, "FHMWR", "21209133741")
ok(r == [{"status": "Ativo", "carteirinha": "FHMWR000747025", "tipo": "Dependente", "contrato": "FHMWR",
          "nome": "GABRYELLA DA COSTA LIMA ARAGAO"}], "só o CPF pedido, com status e carteirinha")
ok(chamadas[-1][0].endswith("/companies/partner-companies/FHMWR/beneficiaries") and chamadas[-1][1]["search"] == "21209133741",
   "a busca da tela Beneficiários, por CPF")
ok(ps._cadastro({}, "FHMWR", "17754534568") == [], "fora do cadastro = lista vazia")
ok(ps._cadastro({}, "FHMWR", "00000000191") is None, "consulta que falhou = None (não é 'não está')")
c = ps.cadastro("FHMWR", ["212.091.337-41", "17754534568", "00000000191"])
ok(c["falharam"] == ["00000000191"] and set(c["vidas"]) == {"21209133741", "17754534568"}, "lote: vidas + falharam")
print(f"\n{falhas} falha(s)")
sys.exit(1 if falhas else 0)
