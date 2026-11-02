"""Regularizar uma onda de exclusão que SUBIU ao portal e ficou como "não enviada" (01/11/2026, inspeção MOV A2/A22).

Caso que originou: onda 2 (dependentes) da transferência 4DFEV→8DAC9, 31/10 03:17 — o upload entrou (movimentação
1337619, portal.envio 472), mas a lista de movimentações deu erro antes de gravar o envio: o lote ficou sem o envio,
sem acompanhamento, e o chamado não soube. A22: ação manual em produção só por ferramenta, com prova e autor — nada
de SQL na mão.

O que faz (cada passo só se ainda não estiver feito; rodar 2x não duplica):
  1. PROVA: o TXT aprovado da onda, no disco do lote, tem o mesmo sha256 de um envio do registro do hapvida-check
     (portal.envio) já NO_PORTAL/ENTREGUE com movimentação. Sem prova, para sem mudar nada.
  2. LOTE: grava o envio no estado.json pelo motor de exclusão (servico.registrar_envio), com "regularizado" (autor,
     motivo, registro de origem) — o lembrete deixa de pedir a onda 2 de novo.
  3. ACOMPANHAMENTO: cria a linha do vigia (#154) igual à da onda 2 automática, ligada ao chamado da onda dos
     titulares; o vigia confere o resultado na Hapvida na próxima rodada.
  4. NOTA no chamado (movimentacoes.evento, pelo envio._evento do portal) com autor, motivo e a movimentação.

Uso (dentro do container do hapvida-check):
  python3 regularizar_envio_exclusao.py --lote <id> --onda dependentes --autor <upn> --motivo "<texto>"           (simula)
  python3 regularizar_envio_exclusao.py ... --aplicar
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

ESTADOS_PROVA = ("NO_PORTAL", "ENTREGUE")


def _servico():
    sys.path.insert(0, os.environ.get("MOTOR_EXCLUSAO_PATH", "/motor"))
    from motor_exclusao import servico
    return servico, servico.Pastas(os.environ["EXCLUSAO_LOTES_DIR"], os.environ.get("EXCLUSAO_PRIVADO_DIR", ""))


def _eventos():
    caminho = os.environ.get("SANEAMENTO_PATH", "/lifetools_service")
    if caminho not in sys.path:
        sys.path.insert(0, caminho)
    from movimentacoes import envio
    return envio


def prova(cur, contrato: str, sha: str) -> dict | None:
    cur.execute("""SELECT id, protocolo, estado, criado_em FROM portal.envio
                    WHERE contrato = %s AND sha256 = %s AND estado = ANY(%s) AND protocolo IS NOT NULL
                    ORDER BY id DESC LIMIT 1""", (contrato, sha, list(ESTADOS_PROVA)))
    r = cur.fetchone()
    return dict(zip(("id", "protocolo", "estado", "criado_em"), r)) if r else None


def regularizar(cur, servico, pastas, lote: str, onda: str, autor: str, motivo: str, aplicar: bool,
                evento=None) -> list[str]:
    """-> o que fez (ou faria). cur: cursor do banco lifetools (sem commit aqui: quem chama decide).
    evento(cur, cid, tipo, detalhe): grava no histórico do chamado (envio._evento)."""
    if len(motivo.strip()) < 10 or "@" not in autor:
        raise SystemExit("informe --autor (upn) e --motivo (o que foi conferido)")
    estado = servico.ler_estado(lote, pastas)
    aprovados = (estado.get("aprovacoes") or [{}])[-1].get("arquivos") or []
    alvo = next((a for a in aprovados if a.get("onda") == onda), None)
    if not alvo:
        raise SystemExit(f"o lote não tem onda '{onda}' aprovada")
    caminho = os.path.join(pastas.lotes, lote, alvo["arquivo"])
    sha = hashlib.sha256(open(caminho, "rb").read()).hexdigest()
    if sha != alvo["hash"]:
        raise SystemExit("o TXT no disco não é o que foi aprovado (hash diferente) — não regularizo")
    p = prova(cur, alvo["contrato"], sha)
    if not p:
        raise SystemExit("sem prova no registro do portal (portal.envio NO_PORTAL/ENTREGUE com movimentação) — não regularizo")
    feito = [f"prova: portal.envio {p['id']} {p['estado']}, movimentação {p['protocolo']}, "
             f"{p['criado_em'].astimezone():%d/%m/%Y %H:%M}, sha {sha[:12]}"]
    envio = {"onda": onda, "contrato": alvo["contrato"], "hash": sha, "arquivo": alvo["arquivo"],
             "linhas": alvo.get("linhas"), "status": 200, "protocolo": str(p["protocolo"]), "por": autor,
             "em": p["criado_em"].astimezone().isoformat(),
             "regularizado": {"por": autor, "em": datetime.now().astimezone().isoformat(), "motivo": motivo,
                              "portal_envio": p["id"]}}
    ja = any(e.get("onda") == onda and e.get("contrato") == alvo["contrato"] for e in estado.get("envios") or [])
    if ja:
        feito.append("lote: envio já estava gravado")
    else:
        feito.append(f"lote: grava o envio da onda {onda} (movimentação {p['protocolo']})")
        if aplicar:
            servico.registrar_envio(lote, envio, pastas)
    cur.execute("""SELECT id, status, solicitante_nome, controle_portal FROM movimentacoes_hapvida_acompanhamento
                    WHERE nome_arquivo = %s ORDER BY id DESC LIMIT 1""", (alvo["arquivo"],))
    acomp = cur.fetchone()
    tit = next((e for e in estado.get("envios") or [] if e.get("onda") == "titulares" and e.get("contrato") == alvo["contrato"]), {})
    cid = None
    if tit.get("arquivo"):
        cur.execute("SELECT mov_chamado_id FROM movimentacoes_hapvida_acompanhamento WHERE nome_arquivo = %s "
                    "ORDER BY id DESC LIMIT 1", (tit["arquivo"],))
        r = cur.fetchone()
        cid = r[0] if r else None
    # 01/11/2026 23h (achado na 1ª execução): o vigia casa a movimentação por controle_portal ou pelo horário de envio
    # (enviado_em) — sem os dois, procura perto da hora da regularização, não acha e marca FALHOU com aviso falso
    if acomp and str(acomp[2] or "").startswith("Regularização") and (acomp[1] == "FALHOU" or not acomp[3]):
        feito.append(f"acompanhamento: conserta o {acomp[0]} criado pela regularização (movimentação e horário do envio)")
        if aplicar:
            cur.execute("""UPDATE movimentacoes_hapvida_acompanhamento
                              SET controle_portal = %s, enviado_em = %s, status = 'AGUARDANDO_CHECAGEM_INICIAL',
                                  detalhe_status = NULL, notificado_em = NULL, proxima_checagem_em = now()
                            WHERE id = %s""", (str(p["protocolo"]), p["criado_em"], acomp[0]))
            if cid and evento:
                evento(cur, cid, "NOTA", {
                    "obs": (f"Desconsiderar o aviso \"não apareceu na lista de movimentações\" da exclusão {alvo['contrato']} "
                            f"(movimentação {p['protocolo']}): foi falha da regularização, que não informou ao vigia a "
                            f"movimentação nem o horário do envio. A movimentação está no portal desde "
                            f"{p['criado_em'].astimezone():%d/%m/%Y %H:%M}; acompanhamento {acomp[0]} corrigido, o vigia "
                            f"confere de novo. {motivo}"),
                    "autor": autor, "regularizado": alvo["arquivo"] + ":conserto", "protocolo_portal": str(p["protocolo"])})
    elif acomp:
        feito.append(f"acompanhamento: já existe ({acomp[0]})")
    else:
        feito.append(f"acompanhamento: cria (vigia confere na próxima rodada), chamado {cid or '—'}")
        if aplicar:  # mesmas colunas da onda 2 automática (verificar_acompanhamentos._enviar_onda2) + como o vigia acha
            cur.execute("""INSERT INTO movimentacoes_hapvida_acompanhamento
                             (codigo_contrato, confirmacao_upload, controle_portal, enviado_em, nome_arquivo, total_vidas,
                              solicitante_upn, solicitante_nome, proxima_checagem_em, mov_chamado_id)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), %s)""",
                        (alvo["contrato"], str(p["protocolo"]), str(p["protocolo"]), p["criado_em"], alvo["arquivo"],
                         alvo.get("linhas"), estado["solicitante_upn"], f"Regularização ({autor})", cid))
    if cid:
        cur.execute("SELECT 1 FROM movimentacoes.evento WHERE chamado_id = %s AND tipo = 'NOTA' "
                    "AND detalhe->>'regularizado' = %s", (cid, alvo["arquivo"]))
        if cur.fetchone():
            feito.append("nota: já registrada no chamado")
        else:
            feito.append(f"nota: registra no chamado {cid}")
            if aplicar and evento:
                evento(cur, cid, "NOTA", {
                    "obs": (f"Exclusão {alvo['contrato']}, onda {onda} ({alvo.get('linhas')} vida): subiu ao portal em "
                            f"{p['criado_em'].astimezone():%d/%m/%Y %H:%M} (movimentação {p['protocolo']}), mas ficou "
                            f"registrada como não enviada (erro na lista de movimentações depois do upload). Regularizada: "
                            f"envio gravado no lote e acompanhamento criado; o vigia confere o resultado na Hapvida. "
                            f"{motivo}"),
                    "autor": autor, "regularizado": alvo["arquivo"], "protocolo_portal": str(p["protocolo"]),
                    "portal_envio": p["id"]})
    else:
        feito.append("nota: lote sem chamado ligado (não registra)")
    return feito


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lote", required=True)
    ap.add_argument("--onda", required=True, choices=("titulares", "dependentes"))
    ap.add_argument("--autor", required=True)
    ap.add_argument("--motivo", required=True)
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()
    import portal_envio
    servico, pastas = _servico()
    conn = portal_envio._conn()
    try:
        with conn, conn.cursor() as cur:
            feito = regularizar(cur, servico, pastas, a.lote, a.onda, a.autor, a.motivo, a.aplicar, _eventos()._evento)
            if not a.aplicar:
                conn.rollback()
    finally:
        conn.close()
    print(json.dumps({"aplicado": a.aplicar, "passos": feito}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
