"""Envio com registro durável (obra 2, 17/10/2026). Usa o banco de verdade com o contrato fictício ZZTST e apaga as
próprias linhas no fim. Saída != 0 = falhou."""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")
import portal_envio as pe  # noqa: E402

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg, flush=True)
    falhas += 0 if cond else 1


def arquivo(nome, conteudo):
    d = tempfile.mkdtemp()
    p = os.path.join(d, nome)
    open(p, "w").write(conteudo)
    return p


def estado(eid):
    c = pe._conn()
    with c.cursor() as cur:
        cur.execute("SELECT estado, protocolo FROM portal.envio WHERE id = %s", (eid,))
        r = cur.fetchone()
    c.close()
    return r


def limpar():
    c = pe._conn()
    with c, c.cursor() as cur:
        cur.execute("DELETE FROM portal.envio WHERE contrato = 'ZZTST'")
    c.close()


limpar()
try:
    a = arquivo("CICLO_999_ZZTST.txt", "linha de teste 1\n")
    e1 = pe.antes("ZZTST", a)
    ok(estado(e1)[0] == "ENVIANDO", "gravado ENVIANDO antes de subir")
    pe.depois(e1, {"status": 200, "body": {"content": "conf-1"}})
    ok(estado(e1)[0] == "ENTREGUE", "200 do upload -> ENTREGUE")
    try:
        pe.antes("ZZTST", arquivo("CICLO_1000_ZZTST.txt", "linha de teste 1\n"))
        ok(False, "reenvio do motor com o mesmo conteúdo devia ser bloqueado")
    except pe.Duplicado as d:
        ok("já foi entregue" in str(d), f"reenvio duplicado bloqueado: {d}")
    e3 = pe.antes("ZZTST", arquivo("tmpabc.txt", "linha de teste 1\n"))
    ok(e3 and estado(e3)[0] == "ENVIANDO", "inclusão da Cora com o mesmo conteúdo: modo sombra, sobe")
    pe.depois(e3, {"status": 500, "erro": "x"})
    ok(estado(e3)[0] == "FALHOU_UPLOAD", "upload sem 200 -> FALHOU_UPLOAD")
    # reconciliação: e1 aparece na lista; e3 não aparece e a lista falha numa rodada
    c = pe._conn()
    with c, c.cursor() as cur:
        cur.execute("UPDATE portal.envio SET criado_em = now() - interval '10 minutes' WHERE contrato = 'ZZTST'")
    c.close()
    pe.reconciliar(listar=lambda ct: None)
    ok(estado(e1)[0] == "INCERTO", "lista do portal não respondeu -> INCERTO (nunca FALHOU)")
    pe.reconciliar(listar=lambda ct: [{"uploadName": "CICLO_999_ZZTST_1234.txt", "controlCode": 7777777, "status": "ProcessFile"}])
    ok(estado(e1) == ("NO_PORTAL", "7777777"), "achado pelo nome na lista -> NO_PORTAL com a movimentação")
    c = pe._conn()
    with c, c.cursor() as cur:
        cur.execute("UPDATE portal.envio SET criado_em = now() - interval '3 hours' WHERE id = %s", (e3,))
    c.close()
    pe.reconciliar(listar=lambda ct: [])
    ok(estado(e3)[0] == "FALHOU", "2h sem aparecer -> FALHOU (sem reenvio automático)")
    pe.conferido("CICLO_999_ZZTST.txt", "8888888")
    ok(estado(e1)[1] == "8888888", "quem achou a movimentação marca conferido")
    # arquivo recusado inteiro pode subir de novo
    c = pe._conn()
    with c, c.cursor() as cur:
        cur.execute("UPDATE portal.envio SET status_portal = 'UnableToStart' WHERE id = %s", (e1,))
    c.close()
    e5 = pe.antes("ZZTST", arquivo("CICLO_1001_ZZTST.txt", "linha de teste 1\n"))
    ok(e5 and estado(e5)[0] == "ENVIANDO", "anterior recusado inteiro (UnableToStart): pode reenviar")
finally:
    limpar()
sys.exit(1 if falhas else 0)
