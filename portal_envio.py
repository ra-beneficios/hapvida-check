"""Obra 2 (17/10/2026) — envio ao portal com registro durável (docs/processos/OBRAS_ESCALA_100K.md no repo lifetools).

Todo upload passa por movimentacao_saude.run_inclusao, que chama `antes()` e `depois()` daqui:
  ENVIANDO (gravado ANTES de subir) -> ENTREGUE (200 do upload) | FALHOU_UPLOAD
  o reconciliador (vigia de 15 min) confere pelo nome na lista de movimentações do portal:
  ENTREGUE/ENVIANDO/FALHOU_UPLOAD achado -> NO_PORTAL (com a movimentação); lista não respondeu -> INCERTO;
  2h sem aparecer -> FALHOU (NUNCA reenvia sozinho — regra da mesa, 17/10).
Duplicado (mesmo conteúdo no mesmo contrato, já entregue nos últimos 7 dias e não recusado inteiro): só bloqueia nos
tipos de ENVIO_BLOQUEIA_DUPLICADO (padrão: REENVIO, o do motor); nos outros fica registrado (modo sombra).
Nada aqui pode derrubar um envio: falha do registro vira só log.
"""
import hashlib
import os
from datetime import datetime

DIAS_DUPLICADO = 7
BLOQUEIA = {t.strip().upper() for t in os.environ.get("ENVIO_BLOQUEIA_DUPLICADO", "REENVIO").split(",") if t.strip()}
FALHA_DEPOIS_H = 2


class Duplicado(Exception):
    """Mesmo conteúdo já entregue no portal nos últimos 7 dias: não sobe de novo."""

    def __init__(self, envio: dict):
        self.envio = envio
        super().__init__(f"o mesmo arquivo já foi entregue ao portal em {envio['criado_em']:%d/%m %H:%M}"
                         + (f" (movimentação {envio['protocolo']})" if envio.get("protocolo") else ""))


def _conn():
    import psycopg2
    return psycopg2.connect(host=os.environ.get("DB_HOST", "postgres"), user=os.environ.get("DB_USER", "radb"),
                            password=os.environ.get("DB_PASSWORD", ""), dbname=os.environ.get("DB_NAME", "lifetools"))


def tipo_do_nome(nome: str) -> str:
    n = (nome or "").upper()
    return "EXCLUSAO" if n.startswith("EXCLUSAO_") else "REENVIO" if n.startswith("CICLO_") else "INCLUSAO"


def antes(contrato: str, caminho: str) -> int | None:
    """Grava ENVIANDO antes do upload. Levanta Duplicado (só nos tipos que bloqueiam). -> id do envio (None se o
    registro falhou — o upload segue)."""
    nome = os.path.basename(caminho)
    try:
        conteudo = open(caminho, "rb").read()
    except OSError:
        return None
    sha = hashlib.sha256(conteudo).hexdigest()
    linhas = sum(1 for l in conteudo.splitlines() if l.strip())
    tipo = tipo_do_nome(nome)
    try:
        conn = _conn()
    except Exception as e:
        print(f"[envio] sem banco para registrar {nome}: {e}")
        return None
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""SELECT id, estado, protocolo, criado_em, status_portal FROM portal.envio
                           WHERE contrato = %s AND sha256 = %s AND criado_em > now() - make_interval(days => %s)
                             AND estado IN ('ENVIANDO', 'ENTREGUE', 'NO_PORTAL', 'INCERTO')
                             AND coalesce(status_portal, '') <> 'UnableToStart'
                           ORDER BY id DESC LIMIT 1""", (contrato, sha, DIAS_DUPLICADO))
            ja = cur.fetchone()
            duplicado = dict(zip(("id", "estado", "protocolo", "criado_em", "status_portal"), ja)) if ja else None
            estado = "DUPLICADO_BLOQUEADO" if duplicado and tipo in BLOQUEIA else "ENVIANDO"
            cur.execute("""INSERT INTO portal.envio (tipo, contrato, nome_portal, sha256, linhas, estado, erro)
                           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (tipo, contrato, nome, sha, linhas, estado,
                         f"igual ao envio {duplicado['id']}" if duplicado else None))
            eid = cur.fetchone()[0]
        if duplicado and tipo in BLOQUEIA:
            raise Duplicado(duplicado)
        if duplicado:
            print(f"[envio] {nome}: mesmo conteúdo do envio {duplicado['id']} ({duplicado['estado']}) — modo sombra, sobe")
        return eid
    except Duplicado:
        raise
    except Exception as e:
        print(f"[envio] não registrei {nome}: {type(e).__name__}: {e}")
        return None
    finally:
        conn.close()


def depois(eid: int | None, resultado: dict) -> None:
    if not eid:
        return
    status = resultado.get("status")
    corpo = resultado.get("body")
    confirmacao = corpo.get("content") if isinstance(corpo, dict) else None
    try:
        conn = _conn()
        with conn, conn.cursor() as cur:
            cur.execute("""UPDATE portal.envio SET estado = %s, http_status = %s, confirmacao = %s, erro = %s,
                           atualizado_em = now() WHERE id = %s""",
                        ("ENTREGUE" if status == 200 else "FALHOU_UPLOAD", status, confirmacao,
                         None if status == 200 else str(resultado.get("erro") or resultado)[:500], eid))
        conn.close()
    except Exception as e:
        print(f"[envio] não atualizei o envio {eid}: {type(e).__name__}: {e}")


def conferido(nome_portal: str, protocolo: str | None, status_portal: str | None = None) -> None:
    """Quem já achou a movimentação na lista (run-inclusao, aprovar, exclusão) avisa aqui."""
    if not protocolo:
        return
    try:
        conn = _conn()
        with conn, conn.cursor() as cur:
            cur.execute("""UPDATE portal.envio SET estado = 'NO_PORTAL', protocolo = %s, status_portal = coalesce(%s, status_portal),
                           conferido_em = now(), atualizado_em = now()
                           WHERE id = (SELECT id FROM portal.envio WHERE nome_portal = %s ORDER BY id DESC LIMIT 1)""",
                        (str(protocolo), status_portal, nome_portal))
        conn.close()
    except Exception as e:
        print(f"[envio] não marquei {nome_portal} como no portal: {e}")


def _achar(movs: list[dict], nome: str) -> dict | None:
    base = os.path.splitext(nome)[0]
    for m in movs:
        if (m.get("uploadName") or "").startswith(base):
            return m
    return None


def reconciliar(listar=None, agora: datetime | None = None) -> list[str]:
    """Confere no portal os envios sem movimentação. listar(contrato) -> lista de movimentações (ou None se a lista
    não respondeu). -> o que mudou (pra log/aviso)."""
    if listar is None:
        import movimentacao_saude as ms

        def listar(contrato):
            d = ms.checar_status_movimentacoes(os.environ.get("GCP_PROJECT", ""), contrato, limit=30)
            movs = (d.get("content") or {}).get("movements") if isinstance(d, dict) else None
            return movs if isinstance(movs, list) else None
    mudou = []
    conn = _conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("""SELECT id, contrato, nome_portal, estado, criado_em FROM portal.envio
                           WHERE estado IN ('ENVIANDO', 'ENTREGUE', 'INCERTO', 'FALHOU_UPLOAD')
                             AND criado_em < now() - interval '2 minutes' AND criado_em > now() - interval '3 days'
                           ORDER BY contrato, id""")
            pendentes = cur.fetchall()
        por_contrato: dict[str, list] = {}
        for p in pendentes:
            por_contrato.setdefault(p[1], []).append(p)
        for contrato, itens in por_contrato.items():
            try:
                movs = listar(contrato)
            except Exception as e:
                print(f"[envio] lista de {contrato} falhou: {e}")
                movs = None
            with conn, conn.cursor() as cur:
                for eid, _, nome, estado, criado in itens:
                    if movs is None:
                        cur.execute("UPDATE portal.envio SET estado = CASE WHEN estado = 'FALHOU_UPLOAD' THEN estado ELSE 'INCERTO' END, "
                                    "verificacoes = verificacoes + 1, atualizado_em = now() WHERE id = %s", (eid,))
                        continue
                    m = _achar(movs, nome)
                    if m:
                        cur.execute("""UPDATE portal.envio SET estado = 'NO_PORTAL', protocolo = %s, status_portal = %s,
                                       conferido_em = now(), atualizado_em = now(), verificacoes = verificacoes + 1 WHERE id = %s""",
                                    (str(m.get("controlCode")), m.get("status"), eid))
                        mudou.append(f"{contrato} {nome}: no portal, movimentação {m.get('controlCode')}")
                        continue
                    idade_h = ((agora or datetime.now(criado.tzinfo)) - criado).total_seconds() / 3600
                    novo = "FALHOU" if idade_h >= FALHA_DEPOIS_H else ("FALHOU_UPLOAD" if estado == "FALHOU_UPLOAD" else "INCERTO")
                    cur.execute("UPDATE portal.envio SET estado = %s, verificacoes = verificacoes + 1, atualizado_em = now() WHERE id = %s",
                                (novo, eid))
                    if novo == "FALHOU":
                        mudou.append(f"{contrato} {nome}: não apareceu no portal em {FALHA_DEPOIS_H}h — FALHOU (não reenvio sozinho)")
    finally:
        conn.close()
    return mudou
