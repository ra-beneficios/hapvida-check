"""Teste simulado da onda 2 automatica do vigia (sem portal, sem banco, sem Teams). Roda na imagem do hapvida-check."""
# Rodar: docker run --rm --network none --entrypoint python -e PYTHONPATH=/app -v $PWD:/app:ro -v /opt/lifetools/motor_exclusao:/motor/motor_exclusao:ro -w /app hapvida-check-hapvida-check tests/teste_onda2_automatica.py
import datetime
import json
import os
import sys
import tempfile
import types

TMP = tempfile.mkdtemp()
os.environ["EXCLUSAO_LOTES_DIR"] = TMP
sys.path.insert(0, "/motor")
from motor_exclusao import servico  # noqa: E402

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg)
    falhas += 0 if cond else 1


AGORA = datetime.datetime.now().astimezone()
TIT = {"contrato": "ZZTST", "onda": "titulares", "hash": "t" * 64, "arquivo": "EXCLUSAO_ZZTST_titulares_tttttttt.txt", "linhas": 3}
DEP = {"contrato": "ZZTST", "onda": "dependentes", "hash": "d" * 64, "arquivo": "EXCLUSAO_ZZTST_dependentes_dddddddd.txt", "linhas": 5}


def novo_lote(nome):
    os.makedirs(os.path.join(TMP, nome, "retorno"), exist_ok=True)
    estado = {"lote": nome, "solicitante_upn": "pos@ra", "aprovacoes": [{"arquivos": [TIT, DEP]}],
              "envios": [{**TIT, "status": 200, "protocolo": "111"}]}
    servico._salvar_estado(estado, servico.Pastas(TMP, TMP))
    return nome


class Cur:
    def __init__(self):
        self.inserts = []

    def execute(self, sql, args=None):
        if sql.strip().startswith("INSERT"):
            self.inserts.append(args)

    def fetchone(self):
        return {"status": "CONCLUIDO", "notificado_em": AGORA - datetime.timedelta(hours=1)}


def preparar(modo):
    """modo: ok | falha. Stub de exclusao_api com o servico real (estado em disco)."""
    chamadas = []
    api = types.ModuleType("exclusao_api")
    api.servico = servico
    api._pastas = lambda: servico.Pastas(TMP, TMP)

    def enviar(lote, onda, hash_txt, upn):
        chamadas.append((lote, onda, hash_txt, upn))
        if modo == "falha":
            raise RuntimeError("a onda dos titulares ainda nao foi processada pela Hapvida")
        envio = {**DEP, "status": 200, "protocolo": "222", "por": upn}
        servico.registrar_envio(lote, envio, api._pastas())
        return envio

    api.enviar = enviar
    sys.modules["exclusao_api"] = api
    return chamadas


import verificar_acompanhamentos as va  # noqa: E402

msgs, posts = [], []
va.notificar = lambda upn, texto: msgs.append((upn, texto)) or True


class Resp:
    ok = True

    def json(self):
        return {"enviado": True}


va.requests = types.SimpleNamespace(post=lambda url, timeout, json: posts.append(json) or Resp())

# A. sucesso: envia sozinho, registra acompanhamento, avisa quem pediu, sem lembrete; 2a rodada nao reenvia
va.ONDA2_AUTOMATICA = True
lote = novo_lote("A_sucesso")
ch = preparar("ok")
cur = Cur()
va.lembrar_ondas_pendentes(cur)
est = servico.ler_estado(lote, servico.Pastas(TMP, TMP))
ok(ch == [(lote, "dependentes", DEP["hash"], "pos@ra")], "A: chamou enviar da onda dos dependentes como o solicitante")
ok(len(cur.inserts) == 1 and cur.inserts[0][1] == "222" and cur.inserts[0][2] == DEP["arquivo"], "A: entrou no acompanhamento com o protocolo")
ok(not any(u == "pos@ra" for u, t in msgs), "A: onda 2 automatica sai em silencio (16/10, Andre: aviso so no final)")
ok(not posts, "A: nenhum cartao de lembrete")
ok([e["onda"] for e in est["envios"]] == ["titulares", "dependentes"], "A: envio gravado no lote (sem sobrescrever o do enviar)")
ok(est["auto_onda2"][0]["ok"] is True, "A: tentativa automatica registrada")
ch.clear(); msgs.clear()
va.lembrar_ondas_pendentes(Cur())
ok(not ch and not posts, "A: 2a rodada nao reenvia nem lembra")

# B. falha: avisa o Andre, manda o cartao com botao; proxima rodada nao tenta sozinho de novo
os.remove(os.path.join(TMP, lote, "estado.json"))
lote = novo_lote("B_falha")
ch = preparar("falha")
msgs.clear(); posts.clear()
va.lembrar_ondas_pendentes(Cur())
est = servico.ler_estado(lote, servico.Pastas(TMP, TMP))
ok(len(ch) == 1, "B: tentou uma vez")
ok(any(u == va.ADMIN_UPN and "não saiu" in t for u, t in msgs), "B: avisou o Andre")
ok(len(posts) == 1 and "Tentei enviar sozinho" in posts[0]["texto"], "B: cartao com botao pro pos-vendas, dizendo que tentou")
ok(est["auto_onda2"][0]["ok"] is False and len(est["lembretes"]) == 1, "B: tentativa (falha) e lembrete gravados")
ch.clear(); posts.clear()
va.lembrar_ondas_pendentes(Cur())
ok(not ch, "B: 2a rodada nao tenta sozinho de novo")
ok(not posts, "B: 2a rodada respeita o intervalo do lembrete (24h)")

# C. chave desligada: comportamento antigo (so lembrete)
os.remove(os.path.join(TMP, lote, "estado.json"))
va.ONDA2_AUTOMATICA = False
lote = novo_lote("C_desligada")
ch = preparar("ok")
posts.clear()
va.lembrar_ondas_pendentes(Cur())
ok(not ch and len(posts) == 1 and "Tentei" not in posts[0]["texto"], "C: chave desligada so manda o lembrete")

# D. simulacao: nao envia
os.remove(os.path.join(TMP, lote, "estado.json"))
va.ONDA2_AUTOMATICA = True
novo_lote("D_simular")
ch = preparar("ok")
r = va.lembrar_ondas_pendentes(Cur(), simular=True)
ok(not ch and any("SIMULACAO onda2-auto" in x for x in r), "D: simulacao nao envia")

print(json.dumps({"falhas": falhas}))
sys.exit(1 if falhas else 0)
