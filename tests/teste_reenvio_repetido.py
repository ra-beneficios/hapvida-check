"""Reenvio automático igual ao que a Hapvida já recusou (02/11/2026, 8DAC9 exec 207 → 210, MOV-000006). Sem portal,
sem banco, sem Teams: tudo simulado. Roda na imagem do hapvida-check. Saída != 0 = falhou."""
# Rodar: docker run --rm --network none --entrypoint python -e PYTHONPATH=/app -w /app <imagem> tests/teste_reenvio_repetido.py
import json
import sys

sys.path[:0] = ["/app", "/lifetools_service"]
import ciclo_criticas as cc  # noqa: E402
import portal_envio  # noqa: E402

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg)
    falhas += 0 if cond else 1


class Cur:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, args=None):
        self.log.append((" ".join(sql.split()), args))

    def fetchone(self):
        return ("0000001" + "X" * 822,)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Conn:
    def __init__(self):
        self.log = []

    def cursor(self):
        return Cur(self.log)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# A. o reenvio herda o chamado do acompanhamento de origem
c = Conn()
cc._registrar_acompanhamento(c, "ZZTST", "conf", "999", "CICLO_1_ZZTST.txt", 1,
                             {"solicitante": "pos@ra", "rodada": 1, "acompanhamento_id": 136}, 1)
sql, args = c.log[-1]
ok("mov_chamado_id" in sql and "WHERE id = %s" in sql and args[-1] == 136,
   "A: acompanhamento do reenvio leva o chamado da origem (o resultado da rodada 2 volta à tela)")

# B. duplicado no reenvio automático: DESCARTADA + as linhas voltam ao responsável (antes: calado)
desfechos, comps, avisos = [], [], []
cc.conectar_saneamento = lambda: Conn()
cc._conn_leitura = lambda: Conn()
cc._execucao = lambda conn, eid: {"status": "CONCLUIDA", "aprovador": "pos@ra", "contrato": "ZZTST",
                                  "controle": "1", "contexto": {}}
cc.linhas_que_derrubam = lambda b: []
cc.DADOS = type("D", (), {"__truediv__": lambda s, o: s})()


class Mov:
    def read_text(self):
        return json.dumps({"createdAt": "2026-11-02T08:00:00"})


cc.DADOS = type("D", (), {"__truediv__": lambda s, o: Mov() if o == "movimento.json" else s})()


class Sessao:
    def __enter__(self):
        return (None, {})

    def __exit__(self, *a):
        return False


cc.coleta_portal.sessao = lambda contrato: Sessao()
cc.coleta_portal.conferir_antes_de_reenviar = lambda *a: {"no_contrato": {}, "em_movimentacao": {}, "via_tela": {}}
cc.persistencia.aprovar = lambda *a, **k: {"ok": True}
cc.persistencia.marcar_desfecho = lambda conn, eid, st, por, det: desfechos.append((eid, st, det))


def dup(*a, **k):
    raise portal_envio.Duplicado({"id": 510, "estado": "NO_PORTAL", "protocolo": "1337744",
                                  "criado_em": __import__("datetime").datetime(2026, 11, 2, 8, 1), "status_portal": None})


cc.ms.run_inclusao = dup
cc.ciclo.complemento_para_responsavel = lambda cl, cs, eid, motivo=None: comps.append(motivo) or {
    "execucao_id": eid, "responsavel": {"upn": "pos@ra", "nome": "Pós"}, "linhas": 1,
    "arquivo_responsavel": "/tmp/x.xlsx", "contrato": "ZZTST", "controle": "1"}
cc._notificar_complemento = lambda comp, repetida=False: avisos.append(repetida)
r = cc.aprovar(210, "AUTOMATICO", "pos@ra", sistema=True)
ok(desfechos and desfechos[-1][1] == "DESCARTADA" and r["enviado"] is False, "B: duplicado não sobe e fica DESCARTADA")
ok(comps and "já recusou" in comps[-1] and avisos == [True] and r["complemento"] == 1,
   "B: as linhas voltam ao responsável com o motivo certo (antes a vida ficava parada sem ninguém saber)")

# C. (A9) resultado da exclusão de chamado vai para o histórico do chamado, não para o Teams do responsável da origem
enviados, no_chamado = [], []
cc._cora = lambda rota, corpo: enviados.append((rota, corpo)) or True
cc._arquivo_b64 = lambda caminho: ""
cc._exclusao_no_chamado = lambda aid, texto: aid == 141 and not no_chamado.append(texto)
r = {"tipo": "EXCLUSAO", "contrato": "2XP6W", "controle": "1337653", "recusadas": 1, "nao_mapeadas": 0,
     "criticas": ["linha 1: Usuário impossibilitado de cancelamento"], "responsavel": {"upn": "resp@ra", "nome": "R"},
     "arquivo_responsavel": "/tmp/x.xlsx"}
cc._notificar(r, {"origem": "VIGIA_EXCLUSAO", "solicitante": "pos@ra", "acompanhamento_id": 141})
ok(no_chamado and "impossibilitado" in no_chamado[0] and "anexo" not in no_chamado[0] and not enviados,
   "C: exclusão de chamado: crítica no histórico do chamado, nada no Teams")
cc._notificar(r, {"origem": "VIGIA_EXCLUSAO", "solicitante": "pos@ra", "acompanhamento_id": 99})
ok(any(rota == "/notificar-arquivo" for rota, _ in enviados), "C: exclusão fora de chamado segue o Teams de sempre")

print(json.dumps({"falhas": falhas}))
sys.exit(1 if falhas else 0)
