"""Linha de base do faturamento mensal (André, 27/10/2026): o que já foi aprovado não pode regredir.
Rodar antes de publicar: docker exec hapvida-check python3 /app/tests/teste_faturamento_linha_base.py

- Relatório de Envios = layout do OUT26 (tabela azul; enviados E não enviados em tabela, linguagem de negócio,
  nunca nome de arquivo nem CSV). NOV26 saiu como lista solta de arquivos porque o layout aprovado morava num
  script avulso (relatorio_out26.py) fora do motor.
- Anexos: zip único acima de 3MB; teto 10MB (Graph aceitou 8MB em 27/10). UTC NOV26 (4,8MB) ficou bloqueada à toa.
- Sessão trocada por outra rodada da mesma conta (KGBQY x YYUQ8, 26/10) reseleciona o contrato."""
import sys
from datetime import date

sys.path.insert(0, "/app")
import documentos_saude as ds  # noqa: E402
import envio_faturamento as ef  # noqa: E402

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg, flush=True)
    falhas += 0 if cond else 1


# --- Relatório de Envios
enviado = {"cliente": "CONSORCIO HC 2", "codigos": "4DFEV+2M2DX", "data_envio": "30/09/2026 18:01",
           "arquivos": ["BOLETO_SAUDE_4DFEV_OUT26.pdf"], "zip": "Documentos_4DFEV_2M2DX_OUT26.zip",
           "emails": ["a@x.com.br"], "pendencias": []}
bloq = {"cliente": "NORMATEL", "codigos": "KGBQY", "prontos": 6,
        "faltas": ["KGBQY: RELATORIO_ANALITICO_SAUDE_KGBQY_NOV26.pdf", "KGBQY: RELATORIO_ANALITICO_SAUDE_KGBQY_NOV26.csv",
                   "KGBQY: LISTA_ATIVOS_SAUDE_KGBQY_NOV26.xlsx", "vencimento não encontrado"]}
titulo, corpo = ef.montar_relatorio("NOV26", [enviado], [bloq], date(2026, 11, 10))
ok(titulo == "Relatório de Envios — Faturamento Novembro/2026 (1 clientes)", f"título no padrão OUT26: {titulo}")
for col in ("Cliente", "Código", "Data de Envio", "Arquivos Enviados", "E-mails enviados", "Arquivos prontos", "Aguardando"):
    ok(f">{col}</th>" in corpo, f"coluna {col}")
ok(corpo.count("<table") == 2, "enviados e não enviados em tabela")
ok("#1f3b5c" in corpo, "cabeçalho azul do OUT26")
ok("compactados em Documentos_4DFEV_2M2DX_OUT26.zip" in corpo, "zip aparece no enviado")
ok("Relatório analítico Saúde, Lista de ativos Saúde" in corpo, "falta em linguagem de negócio, PDF+CSV viram um item")
ok("RELATORIO_ANALITICO_SAUDE_KGBQY" not in corpo and ".csv" not in corpo.split("Não enviados")[1], "sem nome de arquivo/CSV nos pendentes")
_, vazio = ef.montar_relatorio("NOV26", [], [bloq], None)
ok("<table" in vazio.split("Não enviados")[1], "rodada sem envio mantém a tabela de pendentes")

# --- Anexos
ok(ef.LIMITE_ENVIO_BYTES >= 10 * 1024 * 1024, "teto de envio >= 10MB")
grande = [(f"a{i}.pdf", "application/pdf", bytes(1024 * 1024)) for i in range(4)]
envio, _, nz = ef.preparar_anexos(grande, "DAVJV+SY8MI", "NOV26")
ok(len(envio) == 1 and nz == "Documentos_DAVJV_SY8MI_NOV26.zip", "acima de 3MB vira zip único")


# --- Sessão trocada
class R:
    def __init__(s, c, t):
        s.status_code, s.text = c, t


ok(ds._sessao_trocada(R(400, "A obrigação 3504811375 não foi encontrada para o contrato YYUQ8."), "KGBQY"), "outro contrato reseleciona")
ok(not ds._sessao_trocada(R(400, "não foi encontrada para o contrato KGBQY."), "KGBQY"), "mesmo contrato não reseleciona")
ok(ds._sessao_trocada(R(400, "Selecione novamente seu contrato"), "KGBQY"), "'selecione novamente' reseleciona")
ok(not ds._sessao_trocada(R(202, "Aguardando a geração do relatório"), "KGBQY"), "fila da Hapvida não é sessão trocada")

print(f"\n{falhas} falha(s)")
sys.exit(1 if falhas else 0)
