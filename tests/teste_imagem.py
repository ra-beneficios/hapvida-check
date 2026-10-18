"""Fumaça da imagem do hapvida-check (17/10/2026, obra 1): tudo importa, o motor está dentro da imagem e as rotas
de que a Cora depende existem. Roda na imagem nova, sem rede. Saída != 0 = falhou."""
import importlib
import os
import sys

sys.path[:0] = ["/app", "/motor"]
falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg, flush=True)
    falhas += 0 if cond else 1


for m in ["app", "ciclo_criticas", "ciclo_inclusao", "verificar_acompanhamentos", "exclusao_api", "movimentacao_saude",
          "censo_saude", "censo_legado", "copart_saude", "boletos_saude", "documentos_saude", "documentos_legado",
          "enviar_documentos", "faturamento_mensal", "envio_faturamento", "legado_auto", "pedidos_digitacao"]:
    try:
        importlib.import_module(m)
        ok(True, f"importa {m}")
    except Exception as e:
        ok(False, f"importa {m}: {type(e).__name__}: {e}")

ok(os.path.isfile("/lifetools_service/saneamento/motor.py"), "motor de críticas dentro da imagem")
ok(os.path.isfile("/lifetools_service/pipeline/montar_e_auditar.py"), "pipeline (layout oficial) dentro da imagem")
ok(os.path.isdir("/motor/motor_exclusao"), "motor de exclusão dentro da imagem")
import app  # noqa: E402

rotas = {r.path for r in app.app.routes}
for r in ["/run-inclusao", "/ciclo/correcao", "/ciclo/rodar", "/ciclo/aprovar", "/exclusao/enviar", "/saude/ocupado",
          "/inclusao/preparar" if "/inclusao/preparar" in rotas else "/ciclo/movimentacoes"]:
    ok(r in rotas, f"rota {r}")
# 18/10/2026 (8DAC9 1332747): "Em processamento" parado há 24h+ é analisado pelo motor; recente e outros status, não
from datetime import datetime  # noqa: E402
import ciclo_criticas  # noqa: E402

agora = datetime(2026, 10, 18, 8, 0)
ok(ciclo_criticas.parada({"status": "ProcessFile", "updatedAt": "2026-10-16T15:38:52"}, agora), "parada: ProcessFile há 40h")
ok(not ciclo_criticas.parada({"status": "ProcessFile", "updatedAt": "2026-10-18T07:00:00"}, agora), "parada: ProcessFile há 1h, não")
ok(not ciclo_criticas.parada({"status": "UnableToStart", "updatedAt": "2026-10-10T07:00:00"}, agora), "parada: só ProcessFile")
ok(not ciclo_criticas.parada({"status": "ProcessFile"}, agora), "parada: sem updatedAt, não")
# 18/10/2026: resumo dos pedidos via tela em digitação (o que saiu, o que trava, ativo no mesmo contrato)
import pedidos_digitacao  # noqa: E402

t = pedidos_digitacao.resumo([{"contrato": "X", "pedido": "1", "acao": "CONCLUIDO", "ok": True, "status": "Processado", "vidas": ["A"]},
                              {"contrato": "X", "pedido": "2", "acao": "CONCLUIDO", "ok": False, "falta": ["A: anexar RG"], "vidas": ["A"]},
                              {"contrato": "Y", "pedido": "3", "acao": "NAO_CONCLUIR", "motivo": "já ativo", "vidas": ["B"]}])
ok("1 concluído" in t and "anexar RG" in t and "não concluí" in t, "pedidos em digitação: resumo")
ok(pedidos_digitacao.resumo([{"contrato": "X", "acao": "AGUARDAR"}]) == "", "pedidos em digitação: nada a avisar")
from saneamento.motor import VERSAO_MOTOR  # noqa: E402

print("motor", VERSAO_MOTOR)
sys.exit(1 if falhas else 0)
