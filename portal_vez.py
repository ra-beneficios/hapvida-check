"""Obra 3, fase 1 (17/10/2026) — fila única do portal OBSERVANDO (docs/processos/OBRAS_ESCALA_100K.md no lifetools).

Cada login e cada reseleção de contrato da conta do sistema fica em portal.uso, com a origem e quantas outras sessões
estavam abrindo nos 3 minutos antes. Ainda NÃO bloqueia nada: primeiro medir (logins/dia, pico de simultâneos, quem
disputa a conta) e só depois enfileirar. Nunca derruba o chamador.
"""
import inspect
import os
import sys

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
