"""Obra 3, fase 1 (17/10/2026) — fila única do portal OBSERVANDO (docs/processos/OBRAS_ESCALA_100K.md no lifetools).

Cada login e cada reseleção de contrato da conta do sistema fica em portal.uso, com a origem e quantas outras sessões
estavam abrindo nos 3 minutos antes. registrar() só observa e nunca derruba o chamador. 01/11/2026 (inspeção MOV, A6):
vez() é a fila de verdade — as rotinas agendadas que usam a conta Cora rodam dentro dela (o mesmo lock do lifetools).
"""
import contextlib
import functools
import inspect
import os
import sys
import threading

# 01/11/2026 (inspeção MOV, A6): a conta Cora fica presa a UM contrato por sessão; duas rotinas logando juntas trocam o
# contrato uma da outra (30/10 17:29: upload 468 com 400; 31/10 04:00–04:16: "8DAC9 trocado por outra sessão" x10,
# reenvio abortado). O lifetools já fazia fila com este lock (envio.vez_no_portal); as rotinas daqui não respeitavam.
LOCK_PORTAL = 727003
_vez = threading.local()


class PortalOcupado(Exception):
    pass


def tenho_a_vez() -> bool:
    """Esta thread já segura a vez (o envio.vez_no_portal do lifetools, rodando aqui dentro, não pede de novo)."""
    return getattr(_vez, "n", 0) > 0


@contextlib.contextmanager
def vez(espera_min: int = 20):
    """Segura a vez na conta Cora do portal moderno enquanto o bloco roda (o mesmo lock do lifetools). Reentrante na
    mesma thread. PORTAL_VEZ=observar desliga (volta a só registrar). Esperou demais: PortalOcupado (a rotina pula esta
    rodada; o cron tenta de novo)."""
    if os.environ.get("PORTAL_VEZ", "").lower() == "observar" or tenho_a_vez():
        _vez.n = getattr(_vez, "n", 0) + 1
        try:
            yield
        finally:
            _vez.n -= 1
        return
    import psycopg2
    conn = psycopg2.connect(host=os.environ.get("DB_HOST", "postgres"), user=os.environ.get("DB_USER", "radb"),
                            password=os.environ.get("DB_PASSWORD", ""), dbname=os.environ.get("DB_NAME", "lifetools"),
                            connect_timeout=10)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s, 0)", (LOCK_PORTAL,))
            if not cur.fetchone()[0]:
                print(f"[portal_vez] {_origem()}: portal ocupado por outra rotina — esperando a vez (até {espera_min} min)")
                cur.execute(f"SET lock_timeout = {max(1, int(espera_min * 60000))}")
                try:
                    cur.execute("SELECT pg_advisory_lock(%s, 0)", (LOCK_PORTAL,))
                except psycopg2.errors.LockNotAvailable as ex:
                    raise PortalOcupado(f"o portal ficou ocupado por mais de {espera_min} min") from ex
        _vez.n = 1
        try:
            yield
        finally:
            _vez.n = 0
    finally:
        conn.close()        # fechar a conexão solta o lock

ROTINAS = ("verificar_acompanhamentos", "ciclo_inclusao", "ciclo_criticas", "exclusao_api", "app", "faturamento_mensal",
           "boletos_saude", "copart_saude", "documentos_saude", "enviar_documentos", "censo_saude", "atualizar_hapvida",
           "baixar_full_portal", "lote_executar", "coleta_portal")


def _origem() -> str:
    """O primeiro módulo conhecido na pilha (quem abriu a sessão), senão o script."""
    for f in inspect.stack()[2:]:
        nome = os.path.splitext(os.path.basename(f.filename))[0]
        if nome in ROTINAS and nome not in ("coleta_portal", "censo_saude"):
            return f"{nome}.{f.function}"
    return os.path.splitext(os.path.basename(sys.argv[0] or "?"))[0] or "?"


def registrar(tipo: str, contrato: str | None = None) -> None:
    try:
        import psycopg2
        conn = psycopg2.connect(host=os.environ.get("DB_HOST", "postgres"), user=os.environ.get("DB_USER", "radb"),
                                password=os.environ.get("DB_PASSWORD", ""), dbname=os.environ.get("DB_NAME", "lifetools"),
                                connect_timeout=5)
        with conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO portal.uso (tipo, contrato, origem, pid, simultaneos)
                           VALUES (%s, %s, %s, %s, (SELECT count(*) FROM portal.uso WHERE tipo = 'LOGIN'
                                                     AND quando > now() - interval '3 minutes' AND pid <> %s))""",
                        (tipo, contrato, _origem(), os.getpid(), os.getpid()))
        conn.close()
    except Exception as e:
        print(f"[portal_vez] não registrei {tipo}: {type(e).__name__}: {e}")


def na_vez(fn):
    """Decorador das rotas agendadas (rotinas da manhã) que usam a conta Cora: rodam dentro de vez() (A6)."""
    @functools.wraps(fn)
    def rodar(*a, **k):
        with vez(espera_min=30):
            return fn(*a, **k)
    return rodar
