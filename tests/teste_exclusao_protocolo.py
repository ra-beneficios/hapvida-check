"""Exclusão que subiu e não achou o protocolo fica registrada (01/11/2026, inspeção MOV A2). Sem portal, sem banco.
Caso real: onda 2 da 4DFEV em 31/10 03:17 subiu (1337619), a lista do portal deu "Contrato 4DFEV não encontrado" e o
lote ficou como "não enviado" (sem acompanhamento, aviso falso ao André; o lembrete mandaria o botão de novo)."""
# Rodar: docker run --rm --network none --entrypoint python -e PYTHONPATH=/app -e MOTOR_EXCLUSAO_PATH=/motor -w /app <imagem> tests/teste_exclusao_protocolo.py
import json
import os
import sys
import tempfile

TMP = tempfile.mkdtemp()
os.environ["EXCLUSAO_LOTES_DIR"] = TMP
sys.path[:0] = ["/app", "/motor"]
import exclusao_api  # noqa: E402

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg)
    falhas += 0 if cond else 1


def lista_quebrada(*a, **k):
    raise RuntimeError("Contrato 4DFEV não encontrado")


ms = exclusao_api.movimentacao_saude
ms.checar_status_movimentacoes = lista_quebrada
exclusao_api.time.sleep = lambda s: None
ok(exclusao_api._achar_protocolo("ZZTST", "EXCLUSAO_ZZTST_dependentes_x.txt") is None,
   "lista do portal com erro: sem protocolo, sem exceção")

# test_onda2_que_subiu_e_nao_achou_protocolo_fica_registrada
registrados = []
alvo = {"contrato": "ZZTST", "caminho": os.path.join(TMP, "EXCLUSAO_ZZTST_dependentes_x.txt"),
        "arquivo": "EXCLUSAO_ZZTST_dependentes_x.txt", "linhas": 2}
exclusao_api.servico.preparar_envio = lambda *a: alvo
exclusao_api.servico.ler_estado = lambda *a: {"envios": []}
exclusao_api.servico.registrar_envio = lambda lote, envio, pastas: registrados.append(envio)
ms.run_inclusao = lambda *a: {"status": 200, "body": {"content": "ok"}}
r = exclusao_api._enviar("L1", "dependentes", "d" * 64, "pos@ra", None)
ok(r["status"] == 200 and r["protocolo"] is None, "upload 200 com a lista quebrada devolve o envio (sem protocolo)")
ok(len(registrados) == 1 and registrados[0]["onda"] == "dependentes", "envio gravado no lote: não vira 'não enviado'")

print(json.dumps({"falhas": falhas}))
sys.exit(1 if falhas else 0)
