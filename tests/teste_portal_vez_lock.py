"""Vez única na conta Cora (01/11/2026, inspeção MOV A6): as rotinas do hapvida-check esperam o mesmo lock do lifetools.
Usa um banco de TESTE (DB_NAME=lifetools_dev: o lock advisory é por banco e não toca a produção). Saída != 0 = falhou."""
# test_cron_do_hapvida_check_espera_o_lock_do_portal
# Rodar: docker run --rm --network lifetools_default --env-file <(DB_*) -e DB_NAME=lifetools_dev --entrypoint python -w /app <imagem> tests/teste_portal_vez_lock.py
import os
import sys
import threading
import time

sys.path[:0] = ["/app", "/lifetools_service"]
assert os.environ.get("DB_NAME") == "lifetools_dev", "teste de lock só em lifetools_dev"
import psycopg2  # noqa: E402
import portal_vez  # noqa: E402

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg, flush=True)
    falhas += 0 if cond else 1


def segurar(segundos):
    """Outra sessão (o envio do lifetools) com a vez por `segundos`."""
    c = psycopg2.connect(host=os.environ.get("DB_HOST", "postgres"), user=os.environ.get("DB_USER", "radb"),
                         password=os.environ.get("DB_PASSWORD", ""), dbname="lifetools_dev")
    c.autocommit = True
    c.cursor().execute("SELECT pg_advisory_lock(%s, 0)", (portal_vez.LOCK_PORTAL,))
    threading.Timer(segundos, c.close).start()
    return c


# A. ocupado por mais tempo que a espera: PortalOcupado, sem rodar o bloco
segurar(6)
rodou = []
t0 = time.monotonic()
try:
    with portal_vez.vez(espera_min=0.05):
        rodou.append(1)
    ok(False, "A: devia esperar e desistir")
except portal_vez.PortalOcupado:
    ok(not rodou and 2 < time.monotonic() - t0 < 6, f"A: esperou {time.monotonic() - t0:.1f}s e desistiu (a rotina pula a rodada)")
time.sleep(4)

# B. o outro solta: a rotina entra logo depois
segurar(1.5)
t0 = time.monotonic()
with portal_vez.vez(espera_min=1):
    espera = time.monotonic() - t0
    ok(1 <= espera < 5 and portal_vez.tenho_a_vez(), f"B: esperou a vez do envio ({espera:.1f}s) e entrou")
    # C. reentrante na mesma thread, e o vez_no_portal do lifetools rodando aqui dentro não trava
    with portal_vez.vez(espera_min=0.05):
        ok(True, "C: vez() dentro de vez() não trava")
    os.environ.setdefault("MOV_DB_NAME", "lifetools_dev")
    try:
        from movimentacoes import envio
        t1 = time.monotonic()
        with envio.vez_no_portal(espera_min=0.05):
            ok(time.monotonic() - t1 < 1, "C: envio.vez_no_portal do lifetools dentro da rotina não pede a vez de novo")
    except ImportError as e:
        ok(False, f"C: sem o service do lifetools na imagem: {e}")
ok(not portal_vez.tenho_a_vez(), "B: saiu do bloco, soltou a vez")

# D. com a vez solta, outra sessão consegue pegar na hora
c = psycopg2.connect(host=os.environ.get("DB_HOST", "postgres"), user=os.environ.get("DB_USER", "radb"),
                     password=os.environ.get("DB_PASSWORD", ""), dbname="lifetools_dev")
cur = c.cursor()
cur.execute("SELECT pg_try_advisory_lock(%s, 0)", (portal_vez.LOCK_PORTAL,))
ok(cur.fetchone()[0], "D: lock livre depois da rotina")
c.close()

# E. PORTAL_VEZ=observar desliga (volta ao comportamento antigo)
segurar(3)
os.environ["PORTAL_VEZ"] = "observar"
t0 = time.monotonic()
with portal_vez.vez(espera_min=0.05):
    ok(time.monotonic() - t0 < 1, "E: PORTAL_VEZ=observar não espera")
del os.environ["PORTAL_VEZ"]

print({"falhas": falhas})
sys.exit(1 if falhas else 0)
