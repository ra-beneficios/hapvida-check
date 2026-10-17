"""Fila do portal observando (obra 3, 17/10/2026): login e reseleção ficam registrados com origem. Banco de verdade,
contrato fictício ZZTST, apaga as próprias linhas."""
import sys

sys.path.insert(0, "/app")
import portal_vez  # noqa: E402
import portal_envio  # noqa: E402

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg, flush=True)
    falhas += 0 if cond else 1


def abrir_sessao_do_vigia():   # simula quem abre: o nome da função aparece na origem
    portal_vez.registrar("LOGIN", "ZZTST")


abrir_sessao_do_vigia()
portal_vez.registrar("RESELECAO", "ZZTST")
c = portal_envio._conn()
with c, c.cursor() as cur:
    cur.execute("SELECT tipo, origem, simultaneos FROM portal.uso WHERE contrato = 'ZZTST' ORDER BY id")
    r = cur.fetchall()
    cur.execute("DELETE FROM portal.uso WHERE contrato = 'ZZTST'")
c.close()
ok([x[0] for x in r] == ["LOGIN", "RESELECAO"], f"login e reseleção registrados ({r})")
ok(all(x[1] for x in r) and all(x[2] is not None for x in r), "com origem e simultâneos")
sys.exit(1 if falhas else 0)
