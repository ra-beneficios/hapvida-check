"""Copart sem o CSV da Hapvida (André, 17/11/2026): o XLSX sai do PDF oficial da fatura.
Rodar antes de publicar: docker exec hapvida-check python3 /app/tests/teste_copart_pdf.py

Caso real: UTC DAVJV NOV26 (fatura 300083551409, R$ 21.443,40) — o CSV ficou na fila da Hapvida e o PDF
(82 páginas) já estava pronto. Usa o PDF da pasta de faturas (dado de cliente não entra no git)."""
import os
import shutil
import sys
import tempfile

import openpyxl

sys.path.insert(0, "/app")
import faturamento_mensal as fm  # noqa: E402
import padrao_faturamento as pf  # noqa: E402

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg, flush=True)
    falhas += 0 if cond else 1


PDF = "/sharepoint/faturas/UTC OLEO E GAS ENGENHARIA S.A./DAVJV/NOV26/RELATORIO_ANALITICO_COPARTICIPACAO_DAVJV_NOV26.pdf"
if not os.path.exists(PDF):
    sys.exit(f"sem o PDF de referência ({PDF}) — teste não roda")

# --- leitura do PDF
d = pf.copart_pdf_para_linhas(PDF)
ok(d["fatura"] == "300083551409" and d["vencimento"] == "10/11/2026", "fatura e vencimento lidos do PDF")
ok(abs(d["total"] - 21443.40) < 0.01 and abs(d["valor"] - 21443.40) < 0.01, "soma das linhas = valor da fatura (R$ 21.443,40)")
ok(len(d["linhas"]) == 1083, f"1.083 atendimentos (leu {len(d['linhas'])})")
ok(len({l[1] for l in d["linhas"]}) == 540, "540 beneficiários")
ok(all(l[0] and l[1] for l in d["linhas"]), "toda linha com titular e beneficiário (inclusive titular repetido na página seguinte)")
ok(any(l[9] == "CONSULTA EM CONSULTORIO (NO HORARIO NORMAL OU PREESTABELECIDO)" for l in d["linhas"]),
   "descrição quebrada em 2 linhas no PDF volta inteira")
ok(any(l[9] == "CONSULTA MEDICA CONSULTORIO - PACOTE OFTALMOLOGIA (NDI SP)" for l in d["linhas"]),
   "quebra no meio da palavra (OFTALMOLOG|IA) colada")

# --- pasta sem CSV: o PDF é fonte, o XLSX sai dele e o total confere com o boleto
with tempfile.TemporaryDirectory() as pasta:
    nome_pdf = os.path.basename(PDF)
    shutil.copy(PDF, os.path.join(pasta, nome_pdf))
    nomes = [nome_pdf]
    falta = pf.faltando_fontes(nomes, "DAVJV", "NOV26", "SAUDE", True)
    ok("RELATORIO_ANALITICO_COPARTICIPACAO_DAVJV_NOV26.csv" not in falta, "sem CSV, o PDF da copart vale como fonte")
    ok("RELATORIO_ANALITICO_SAUDE_DAVJV_NOV26.csv" in falta, "a mensalidade continua exigindo o CSV")
    ok(fm.gerar_xlsx_pasta(pasta) == 1, "gerar_xlsx_pasta faz o XLSX da copart pelo PDF")
    xlsx = os.path.join(pasta, "RELATORIO_ANALITICO_COPARTICIPACAO_DAVJV_NOV26.xlsx")
    ws = openpyxl.load_workbook(xlsx).active
    ok(ws.cell(5, 1).value == "Titular" and ws.cell(5, 13).value == "Valor (R$)", "cabeçalho das colunas na linha 5")
    ok(ws.cell(ws.max_row, 1).value == "TOTAL" and abs(ws.cell(ws.max_row, 13).value - 21443.40) < 0.01, "linha de total = R$ 21.443,40")
    ok(fm.gerar_xlsx_pasta(pasta) == 0, "não refaz o XLSX se o PDF não mudou")
    ok(pf.conferir_valores(pasta, "DAVJV", "NOV26", "SAUDE", True, {"COPARTICIPACAO": {21443.40}}) == [],
       "total do PDF confere com o boleto")
    prob = pf.conferir_valores(pasta, "DAVJV", "NOV26", "SAUDE", True, {"COPARTICIPACAO": {100.0}})
    ok(len(prob) == 1 and "≠ boleto" in prob[0], "total do PDF diferente do boleto bloqueia")
    # chegou o CSV: ele volta a ser a fonte (o PDF não gera mais nada)
    open(os.path.join(pasta, "RELATORIO_ANALITICO_COPARTICIPACAO_DAVJV_NOV26.csv"), "w").write("x")
    ok(fm._xlsx_copart_do_pdf(pasta) == 0, "com o CSV na pasta, o XLSX não sai do PDF")

# --- PDF que não é o relatório de copart: falha segura
outro = "/sharepoint/faturas/UTC OLEO E GAS ENGENHARIA S.A./DAVJV/NOV26/BOLETO_SAUDE_DAVJV_NOV26.pdf"
if os.path.exists(outro):
    try:
        pf.copart_pdf_para_linhas(outro)
        ok(False, "boleto lido como copart")
    except ValueError:
        ok(True, "PDF que não é a copart não vira XLSX (ValueError)")

print(f"\n{'TUDO OK' if not falhas else f'{falhas} FALHA(S)'}")
sys.exit(1 if falhas else 0)
