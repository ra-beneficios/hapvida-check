"""23/11/2026 (André, MOV-10 l.5/6 João Felix e Matheus): o dependente que a planilha pede como titular aparece "Ativo"
como DEPENDENTE no cadastro — a rotina da inclusão gravava a linha de titular como INCLUSAO_CONFIRMADA na base. Agora só
vale ativo no papel pedido. Sem rede: a sessão e o cadastro são simulados."""
import contextlib
import sys
sys.path.insert(0, "/app")
import ciclo_inclusao as ci  # noqa: E402

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg, flush=True)
    falhas += 0 if cond else 1


CAD = {"11111111111": [{"_status_lista": "Ativo", "_tipo_lista": "Dependente"}],     # dependente, pedido como titular
       "22222222222": [{"_status_lista": "Ativo", "_tipo_lista": "Titular"}],
       "33333333333": [{"_status_lista": "Ativo", "_tipo_lista": "Dependente"}],     # dependente pedido como dependente
       "44444444444": [{"_status_lista": "Ativo"}]}                                  # sem tipo: vale o status
ci.coleta_portal.sessao = lambda c: contextlib.nullcontext(({}, {}))
ci.coleta_portal.certificados_do_cpf = lambda page, h, c, cpf: CAD.get(cpf)
ci._cancelados_novos = lambda *a: {}
inc = {"id": 1, "contrato": "WP64K", "ativas": {}, "vidas": [
    {"cpf": "11111111111", "papel": "01"}, {"cpf": "22222222222", "papel": "01"},
    {"cpf": "33333333333", "papel": "04"}, {"cpf": "44444444444", "papel": "01"}]}
novas, _ = ci.conferir_ativas(inc, simular=True)
ok("11111111111" not in novas, "dependente na Hapvida pedido como titular NÃO é ativa (não vira INCLUSAO_CONFIRMADA)")
ok("22222222222" in novas, "titular ativo como titular é ativa")
ok("33333333333" in novas, "dependente ativo como dependente é ativa")
ok("44444444444" in novas, "sem o tipo no cadastro, vale o status")
print(f"\n{falhas} falha(s)")
sys.exit(1 if falhas else 0)
