"""Complemento automático da coparticipação (André, 17/11/2026: "diária do 27 ao 10, complemento automático").
Rodar antes de publicar: docker exec hapvida-check python3 /app/tests/teste_complemento_copart.py

Caso real NOV26: DAVJV, X98VE e YXF01 saíram em 27/10 sem a copart (a Hapvida emitiu depois) e o motor não voltava
em grupo já enviado. Lê a pasta real da UTC (só leitura)."""
import sys
from datetime import date

sys.path.insert(0, "/app")
import complemento_copart as cc  # noqa: E402

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg, flush=True)
    falhas += 0 if cond else 1


# --- período: do dia 27 ao dia 10
ok(all(cc.no_periodo(date(2026, 11, d)) for d in (27, 28, 30)), "roda do dia 27 ao fim do mês")
ok(all(cc.no_periodo(date(2026, 12, d)) for d in (1, 5, 10)), "roda do dia 1 ao dia 10")
ok(not any(cc.no_periodo(date(2026, 12, d)) for d in (11, 20, 26)), "não roda do dia 11 ao 26")

# --- quem precisa de complemento
log = [
    {"cliente": "UTC", "codigos": "DAVJV+SY8MI", "status": "enviado", "data_envio": "27/10/2026 14:46",
     "arquivos": ["BOLETO_SAUDE_DAVJV_NOV26.pdf", "BOLETO_ODONTO_SY8MI_NOV26.pdf"]},
    {"cliente": "HC2", "codigos": "4DFEV+2M2DX", "status": "enviado", "data_envio": "03/11/2026 09:39",
     "arquivos": ["BOLETO_SAUDE_4DFEV_NOV26.pdf", "BOLETO_COPARTICIPACAO_4DFEV_NOV26.pdf"]},
    {"cliente": "X", "codigos": "X98VE", "status": "enviado", "data_envio": "27/10/2026 14:46",
     "arquivos": ["BOLETO_SAUDE_X98VE_NOV26.pdf"]},
    {"cliente": "X", "codigos": "X98VE", "status": "complemento", "manual": True,
     "arquivos": ["BOLETO_COPARTICIPACAO_X98VE_NOV26.pdf"]},
]
saude = {"DAVJV": {"SAUDE", "COPARTICIPACAO"}, "4DFEV": {"SAUDE", "COPARTICIPACAO"},
         "X98VE": {"SAUDE", "COPARTICIPACAO"}, "KGBQY": {"SAUDE", "COPARTICIPACAO"}, "SY8MI": {"SAUDE"}}
p = cc.pendentes(log, saude)
ok([c for c, _ in p] == ["DAVJV"], f"só a copart emitida depois do envio (deu {[c for c, _ in p]})")
ok(p and p[0][1]["codigos"] == "DAVJV+SY8MI", "volta o envio original (grupo e contatos do grupo)")
ok("4DFEV" not in [c for c, _ in p], "copart que foi no envio do mês não repete")
ok("X98VE" not in [c for c, _ in p], "complemento já feito (inclusive manual) não repete")
ok("KGBQY" not in [c for c, _ in p], "grupo ainda não enviado fica com o envio do mês")

# --- arquivos: pasta real da UTC NOV26 (tem o analítico da copart, não tem boleto/NF da copart)
pasta = "/sharepoint/faturas/UTC OLEO E GAS ENGENHARIA S.A./DAVJV/NOV26"
anexos, faltas, pend = cc.anexos_copart(pasta, "DAVJV", "NOV26", {"COPARTICIPACAO": {21443.40}}, date(2026, 11, 3))
nomes = [a[0] for a in anexos]
ok(all("_COPARTICIPACAO_" in n for n in nomes), "só arquivos da copart vão no complemento")
ok("RELATORIO_ANALITICO_COPARTICIPACAO_DAVJV_NOV26.xlsx" in nomes, "XLSX da copart (feito do PDF) vai junto")
ok("BOLETO_COPARTICIPACAO_DAVJV_NOV26.pdf" in faltas, "sem o boleto da copart não manda")
ok(not any("≠ boleto" in f for f in faltas), "total do relatório confere com o boleto da copart")
_, faltas_dia5, pend5 = cc.anexos_copart(pasta, "DAVJV", "NOV26", {"COPARTICIPACAO": {21443.40}}, date(2026, 11, 5))
ok("NFE_COPARTICIPACAO_DAVJV_NOV26.pdf" in faltas and "NFE_COPARTICIPACAO_DAVJV_NOV26.pdf" not in faltas_dia5
   and pend5 == ["NFE_COPARTICIPACAO_DAVJV_NOV26.pdf"], "NF que não saiu segura até o dia 4 e vira pendência do dia 5 ao 10")
_, faltas_errado, _ = cc.anexos_copart(pasta, "DAVJV", "NOV26", {"COPARTICIPACAO": {100.0}}, date(2026, 11, 3))
ok(any("≠ boleto" in f for f in faltas_errado), "total diferente do boleto bloqueia")

# --- Legado: só o PDF da copart na Troca de Arquivos (sem o CSV) - boleto e mês saem do PDF
import glob  # noqa: E402
import legado_auto  # noqa: E402
Z8 = glob.glob("/sharepoint/faturas/*/Z8TUR/NOV26/RELATORIO_ANALITICO_COPARTICIPACAO_Z8TUR_NOV26.pdf")
if Z8:
    pdf = open(Z8[0], "rb").read()
    arqs = {"EMPRESA_FM_Z8TUR_REMESSA_123_PSICO.PDF": pdf}
    r = legado_auto.escolher_relatorios(arqs, "Z8TUR", 11, 2026)
    ok(r["copart"].get("boletos") == {"1010745043073"} and r["copart"].get("pdf") == pdf and r["copart"].get("csv") is None,
       "Legado sem o CSV: o PDF da copart do mês identifica o boleto da copart")
    r = legado_auto.escolher_relatorios(arqs, "Z8TUR", 12, 2026)
    ok(not r["copart"] and r["outros_meses"], "PDF da copart de outro mês não vale")
    csv = open(Z8[0][:-4] + ".csv", "rb").read()
    r = legado_auto.escolher_relatorios({**arqs, "EMPRESA_FM_PSICO_Z8TUR_REMESSA_123.CSV": csv}, "Z8TUR", 11, 2026)
    ok(r["copart"].get("csv") == csv and not r["copart"].get("so_pdf"), "com o CSV na Troca, o CSV continua sendo a fonte")

# --- e-mail
corpo = cc.corpo_cliente(log[0], "DAVJV", "10/11/2026", [{"nome": "BOLETO_COPARTICIPACAO_DAVJV_NOV26.pdf", "descricao": "Boleto"}], "")
ok("coparticipação" in corpo and "27/10/2026" in corpo and "10/11/2026" in corpo, "e-mail do cliente diz o que é e o vencimento")
ok("HTTP" not in corpo and "portal" not in corpo.lower(), "nada técnico no e-mail do cliente")
interno = cc.corpo_interno("NOV26", [], [{"cliente": "UTC", "codigo": "DAVJV", "faltas": ["BOLETO"]}], True)
ok("vencimento" in interno, "no dia 10 o aviso interno diz o que não saiu")

print(f"\n{'TUDO OK' if not falhas else f'{falhas} FALHA(S)'}")
sys.exit(1 if falhas else 0)
