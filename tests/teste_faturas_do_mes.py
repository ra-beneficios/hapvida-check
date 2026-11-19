"""Escolha das faturas do mês no download do portal novo (19/11/2026, MID YXF01 / UTC DAVJV / THREENG X98VE):
a copart de dezembro emitida não pode esconder a de novembro.
Rodar: docker exec hapvida-check python3 /app/tests/teste_faturas_do_mes.py"""
import sys
from datetime import date

sys.path.insert(0, "/app")
from enviar_documentos import escolher_faturas_do_mes as esc  # noqa: E402

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg, flush=True)
    falhas += 0 if cond else 1


M, C = "MENSALIDADE PLANO SAUDE", "CARNET COMPLEMENTAR-COPARTICIPACAO"
davjv = [{"bill_type": M, "numero": "300081682654", "vencimento": date(2026, 11, 10)},
         {"bill_type": C, "numero": "300086783440", "vencimento": date(2026, 12, 10)},
         {"bill_type": C, "numero": "300083551409", "vencimento": date(2026, 11, 10)},
         {"bill_type": M, "numero": "30007", "vencimento": date(2026, 10, 10)}]
r = {f["bill_type"]: f["numero"] for f in esc(davjv)}
ok(r == {M: "300081682654", C: "300083551409"}, f"copart de novembro escolhida mesmo com a de dezembro emitida ({r})")
r = {f["bill_type"]: f["numero"] for f in esc(davjv[:1] + davjv[3:] + [{"bill_type": C, "numero": "x", "vencimento": date(2026, 10, 10)}])}
ok(r == {M: "300081682654"}, "copart de mês anterior não entra")
r = {f["bill_type"]: f["numero"] for f in esc(davjv + [{"bill_type": M, "numero": "dez", "vencimento": date(2026, 12, 10)}])}
ok(r == {M: "dez", C: "300086783440"}, "saiu a mensalidade de dezembro: o mês passa a ser dezembro")
ok(esc([]) == [], "sem fatura, nada")
print(f"\n{'TUDO OK' if not falhas else f'{falhas} FALHA(S)'}")
sys.exit(1 if falhas else 0)
