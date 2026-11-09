"""08/11/2026 (auditoria MOV): críticas por vida (resposta real do pedido 69829162) e tipos de inclusão da tela. Sem rede."""
import sys
sys.path.insert(0, "/app")
import pedidos_situacao as ps  # noqa: E402

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg, flush=True)
    falhas += 0 if cond else 1


ISS = [{"type": "Holder", "health": {"dependentControlNumber": 0, "issues": [{"code": "156", "message": "156e-Titular ja cadastrado. Cod: 2XP6W"}], "documentIssues": []}},
       {"type": "Dependent", "health": {"dependentControlNumber": 82980, "issues": [{"code": "07705", "message": "07705D - REINCLUSAO"}],
                                        "documentIssues": [{"message": "FALTA CERTIDAO"}]}}]
ok(ps.criticas_da_vida(ISS, "0") == ["156e-Titular ja cadastrado. Cod: 2XP6W"], "titular só com a crítica dele")
ok(ps.criticas_da_vida(ISS, "82980") == ["07705D - REINCLUSAO", "FALTA CERTIDAO"], "dependente com a dele + documento")
ok(set(ps.TIPOS_INCLUSAO) == {"Inclusion", "DependentInclusion", "Transfer"}, "inclusão de dependente e transferência contam")
print(f"\n{falhas} falha(s)")
sys.exit(1 if falhas else 0)
