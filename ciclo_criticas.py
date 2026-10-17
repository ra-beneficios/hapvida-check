"""Ciclo de críticas — lado do portal (pedido do André, 08/10/2026).

O motor mora no repo lifetools (`service/saneamento/`, montado só-leitura em /lifetools_service). Aqui só
o que precisa do portal ou da Cora: coletar a movimentação, chamar o ciclo, avisar quem precisa e, depois
do clique do pós-vendas que pediu, subir o reenvio (também o da planilha de correção devolvida).

Quem dispara:
  - verificar_acompanhamentos.py (vigia #154/#156): movimentação da Cora chegou a status final;
  - a Cora, menu "Analisar críticas": POST /ciclo/movimentacoes (lista do dia) e /ciclo/rodar;
  - a Cora, cartão de aprovação: POST /ciclo/aprovar e /ciclo/descartar.

Regras (decisões do André, 08-09/10):
  - revisão e aprovação SEMPRE do pós-vendas que pediu (sem solicitante, o responsável do contrato) — "nada pode
    engargalar em mim" (09/10). Reenvio só com o clique dele; antes de subir, reconfere no portal se alguém da carga
    já ficou ativo desde a análise (se sim, não sobe e pede nova análise);
  - o que o motor não resolve vai pro mesmo pós-vendas (planilha CORRECAO_MOTOR), nunca pro cliente;
  - da 3ª rodada em diante o motor não propõe reenvio (proteção contra loop);
  - exclusão: layout de 189 posições, sem regra de correção ainda (nenhum caso real com crítica até 08/10):
    classifica cada crítica pelo catálogo e manda pro responsável; lacuna do catálogo vai pro André.
"""
import base64
import json
import os
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path

import psycopg2
import requests

import movimentacao_saude as ms

sys.path.insert(0, os.environ.get("SANEAMENTO_PATH", "/lifetools_service"))
from saneamento import ciclo, coleta_portal, correcao, layout, persistencia  # noqa: E402
from saneamento.catalogo import classificar  # noqa: E402
from saneamento.cli import conectar as conectar_saneamento  # noqa: E402
from saneamento.ferramentas import coletar_ceps  # noqa: E402

DADOS = Path(os.environ.get("CICLO_DADOS", "/dados_motor"))
CORA = os.environ.get("CORA_BASE_URL", "http://cora:3978")
STATUS_FINAIS = {"FileProcessed", "FileProcessedWithReview", "ProcessFileWithPendency"}
STATUS_PT = {"FileProcessed": "Processado sem críticas", "FileProcessedWithReview": "Finalizado com críticas",
             "ProcessFileWithPendency": "Processamento com pendência", "ProcessFile": "Em processamento",
             "UnableToStart": "Ainda não iniciou"}


def linhas_que_derrubam(conteudo: bytes) -> list[str]:
    """Portão do layout de inclusão (829 posições) — ver saneamento.layout.portao_upload."""
    out = []
    for i, l in enumerate(conteudo.decode("latin-1").splitlines(), 1):
        if l.strip():
            out += [f"linha {i}: {e}" for e in layout.portao_upload(l)]
    return out


class Recusa(Exception):
    """Regra de negócio (não é o aprovador, situação mudou...): vira 409 com o texto pra Cora mostrar."""


def _conn_leitura():
    return psycopg2.connect(host=os.environ.get("DB_HOST", "postgres"), user=os.environ.get("DB_USER", "radb"),
                            password=os.environ.get("DB_PASSWORD", ""), dbname=os.environ.get("DB_NAME", "lifetools"))


def _cora(rota: str, corpo: dict) -> bool:
    try:
        return requests.post(f"{CORA}{rota}", json=corpo, timeout=30).ok
    except Exception as e:
        print(f"[ciclo] falha ao chamar a Cora {rota}: {type(e).__name__}")
        return False


def _arquivo_b64(caminho: str) -> str:
    return base64.b64encode(Path(caminho).read_bytes()).decode()


# ── listar ───────────────────────────────────────────────────────────────────────────────────────
def movimentacoes_do_dia(contrato: str, dia: str) -> list[dict]:
    """Movimentações do contrato criadas no dia (AAAA-MM-DD), mais recentes primeiro. Sem PII."""
    with coleta_portal.sessao(contrato) as (page, headers):
        movs = coleta_portal.listar_movimentacoes(page, headers, contrato)
    out = []
    for m in movs:
        if (m.get("createdAt") or "")[:10] != dia:
            continue
        nome = m.get("uploadName") or ""
        out.append({"controle": str(m["controlCode"]), "criada_em": m.get("createdAt"), "status": m.get("status"),
                    "status_legivel": STATUS_PT.get(m.get("status"), m.get("status")),
                    "final": m.get("status") in STATUS_FINAIS,
                    "tipo": "EXCLUSAO" if nome.upper().startswith("EXCLUSAO_") else "INCLUSAO",
                    "arquivo": nome, "rejeitadas_arquivo": int(m.get("totalShippingReviews") or 0)})
    return sorted(out, key=lambda x: x["criada_em"] or "", reverse=True)


# ── rodar ────────────────────────────────────────────────────────────────────────────────────────
def rodar(contrato: str, controles: list[str], contexto: dict, notificar: bool = True) -> list[dict]:
    """Coleta (1 login) + ciclo pra cada movimentação. contexto: origem, solicitante, rodada, acompanhamento_id."""
    contrato = contrato.upper()
    pasta_contrato = DADOS / contrato
    coletadas, exclusoes = [], []
    with coleta_portal.sessao(contrato) as (page, headers):
        movs = {str(m["controlCode"]): m for m in coleta_portal.listar_movimentacoes(page, headers, contrato)}
        cpfs = set()
        for cc in controles:
            m = movs.get(str(cc))
            if not m:
                raise Recusa(f"a movimentação {cc} não aparece na lista do contrato {contrato} no portal")
            if m.get("status") not in STATUS_FINAIS:
                raise Recusa(f"a movimentação {cc} ainda está \"{STATUS_PT.get(m.get('status'), m.get('status'))}\" "
                             "na Hapvida — o resultado só vale depois que ela termina de processar")
            coleta_portal.coletar_movimentacao(page, headers, contrato, m, DADOS)
            pasta = pasta_contrato / str(cc)
            if (m.get("uploadName") or "").upper().startswith("EXCLUSAO_"):
                exclusoes.append((str(cc), pasta))
                continue
            coletadas.append((str(cc), pasta))
            cpfs |= coleta_portal.cpfs_para_certificar(pasta)
        if cpfs:
            coleta_portal.coletar_certificados(page, headers, contrato, pasta_contrato, cpfs, refrescar=True)
    conn_l, conn_s = _conn_leitura(), conectar_saneamento()
    try:
        if coletadas:
            coletar_ceps.coletar(pasta_contrato, conn_l)
        por_mov = contexto.get("escopo_por_mov") or {}
        resumos = [ciclo.executar_ciclo(str(p), conn_l, conn_s, {**contexto, "escopo": por_mov.get(cc)}) for cc, p in coletadas]
        resumos += [_ciclo_exclusao(contrato, cc, p, conn_l, contexto.get("solicitante")) for cc, p in exclusoes]
    finally:
        conn_l.close()
        conn_s.close()
    for r in resumos:
        print("[ciclo]", ciclo.resumo_json(r) if "execucao_id" in r else json.dumps({k: r[k] for k in ("contrato", "controle", "tipo")}))
        if r.get("reenvio_automatico"):
            # PROCESSO_INCLUSAO_HAPVIDA.md (15/10/2026), passo 12: o motor reenvia o que corrigiu com fonte certa, sem
            # aprovação e sem avisar o pós-vendas. Falha técnica vai pro André; quem já entrou sai da carga.
            try:
                r["reenvio"] = aprovar(r["execucao_id"], "AUTOMATICO", r["responsavel"]["upn"], sistema=True)
            except Recusa as erro:
                r["reenvio"] = {"enviado": False, "erro": str(erro)}
                _cora("/notificar", {"upn": ciclo.ADMIN, "texto": f"⚠️ Reenvio automático do motor não subiu "
                                     f"({r['contrato']}/{r['controle']}, execução {r['execucao_id']}): {erro}"})
            print("[ciclo] reenvio automático", r["execucao_id"], json.dumps(r["reenvio"], default=str)[:300])
        if notificar:
            _notificar(r, contexto)
    return resumos


def _ciclo_exclusao(contrato: str, controle: str, pasta: Path, conn_l, solicitante: str | None) -> dict:
    vidas = json.loads((pasta / "vidas.json").read_text())
    itens = []
    for v in vidas:
        if v.get("status") == "Processed":
            continue
        textos = [r if isinstance(r, str) else (r.get("description") or r.get("message") or "") for r in v.get("reviews") or []]
        cods = [classificar(t, "VIDA").codigo for t in textos] or ["SEM_CRITICA"]
        itens.append({"linha": str(v.get("sequentialNumber")).zfill(7), "nome": (v.get("life") or {}).get("name"),
                      "status": v.get("status"), "criticas": " | ".join(textos), "categorias": ", ".join(cods)})
    resp = ciclo.pos_vendas_do_pedido(conn_l, contrato, solicitante)
    arq = None
    if itens:
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Exclusões não feitas"
        ws.append(["Linha", "Nome", "Status na Hapvida", "Crítica da Hapvida", "Categoria (motor)", "O que foi feito"])
        for i in itens:
            ws.append([i["linha"], i["nome"], i["status"], i["criticas"], i["categorias"], ""])
        arq = DADOS / "_saidas" / "ciclo" / f"exclusao_{contrato}_{controle}.xlsx"
        arq.parent.mkdir(parents=True, exist_ok=True)
        wb.save(arq)
        os.chmod(arq, 0o600)
    return {"tipo": "EXCLUSAO", "contrato": contrato, "controle": controle, "recusadas": len(itens),
            "nao_mapeadas": sum("CRITICA_NAO_MAPEADA" in i["categorias"] for i in itens),
            "responsavel": resp, "arquivo_responsavel": str(arq) if arq else None}


def _texto_resumo(r: dict) -> str:
    c = r["contadores"]
    disp = c["disposicao"]
    partes = [f"📋 **Análise de críticas — {r['contrato']}, movimentação {r['controle']}**",
              f"{c['linhas']} linha(s): **{r['aceitas']} aceita(s)** pela Hapvida, {r['removidas']} que não precisam de nada "
              f"(já ativas, repetidas ou com pedido aberto)."]
    if c["carga_com_sugeridas"]:
        partes.append(f"🔧 O motor corrigiu **{c['carga_com_sugeridas']} linha(s)** "
                      f"({c['carga_automatica']} automática(s)) — o cartão de aprovação do reenvio foi para "
                      f"**{r['responsavel']['nome']}**.")
    if c["reenvio_bloqueado_por_rodada"]:
        partes.append(f"🔁 Esta já é a rodada {r['rodada']} de reenvio: o motor não reenvia mais sozinho, tudo vai pro responsável.")
    if r["pendencias"] or r["acao_portal"]:
        partes.append(f"📨 {r['pendencias']} correção(ões) e {r['acao_portal']} ação(ões) no portal foram para "
                      f"**{r['responsavel']['nome']}**.")
    if not (c["carga_com_sugeridas"] or r["pendencias"] or r["acao_portal"]):
        partes.append("✅ Nada pendente nesta movimentação.")
    if c.get("nao_mapeadas"):
        partes.append(f"⚠️ {c['nao_mapeadas']} crítica(s) que o motor ainda não conhece — avisei o André.")
    return "\n\n".join(partes)


def _notificar(r: dict, contexto: dict) -> None:
    solicitante = contexto.get("solicitante")
    if r.get("tipo") == "EXCLUSAO":
        texto = (f"📋 **Exclusão {r['contrato']}, movimentação {r['controle']}**: "
                 + (f"{r['recusadas']} vida(s) não foram excluídas pela Hapvida — lista no anexo. Por enquanto o motor "
                    "não corrige exclusão sozinho: cada caso precisa de alguém."
                    if r["recusadas"] else "todas as vidas foram excluídas. ✅"))
        if r["arquivo_responsavel"]:
            _cora("/notificar-arquivo", {"upn": r["responsavel"]["upn"], "texto": texto,
                                         "arquivo_base64": _arquivo_b64(r["arquivo_responsavel"]),
                                         "nome_arquivo": Path(r["arquivo_responsavel"]).name})
        if solicitante and solicitante.lower() != r["responsavel"]["upn"].lower():
            _cora("/notificar", {"upn": solicitante, "texto": texto})
        if r["nao_mapeadas"]:
            _cora("/notificar", {"upn": ciclo.ADMIN, "texto": f"🧩 Lacuna no catálogo do motor: {r['nao_mapeadas']} "
                                 f"crítica(s) de exclusão sem mapeamento ({r['contrato']}/{r['controle']})."})
        return
    resp = r["responsavel"]
    if contexto.get("origem") == "VIGIA_INCLUSAO":
        # passos 12 e 14: silêncio pro pós-vendas; só o arquivo do lote com o que o motor não resolveu
        if r["arquivo_responsavel"]:
            _cora("/notificar-arquivo", {
                "upn": resp["upn"],
                "texto": f"📋 **Lote {r['controle']} ({r['contrato']})**: {r['pendencias'] + r['acao_portal']} vida(s) que o "
                         "motor não conseguiu resolver. Corrija só as células em **vermelho** e me devolva o arquivo "
                         "escrevendo **correção motor de críticas**.",
                "arquivo_base64": _arquivo_b64(r["arquivo_responsavel"]),
                "nome_arquivo": Path(r["arquivo_responsavel"]).name})
        if r["contadores"].get("nao_mapeadas"):
            _cora("/notificar", {"upn": ciclo.ADMIN, "texto": f"🧩 Lacuna no catálogo do motor: {r['contadores']['nao_mapeadas']} "
                                 f"crítica(s) sem mapeamento ({r['contrato']}/{r['controle']}, execução {r['execucao_id']})."})
        return
    texto = _texto_resumo(r)
    if r["arquivo_responsavel"]:
        _cora("/notificar-arquivo", {
            "upn": resp["upn"],
            "texto": texto + "\n\nNo anexo: corrija só as células em **vermelho** e me devolva o arquivo escrevendo "
                             "**correção motor de críticas**.",
            "arquivo_base64": _arquivo_b64(r["arquivo_responsavel"]),
            "nome_arquivo": Path(r["arquivo_responsavel"]).name})
    if solicitante and solicitante.lower() != resp["upn"].lower():
        _cora("/notificar", {"upn": solicitante, "texto": texto})
    if r["contadores"].get("nao_mapeadas"):
        _cora("/notificar", {"upn": ciclo.ADMIN, "texto": f"🧩 Lacuna no catálogo do motor: {r['contadores']['nao_mapeadas']} "
                             f"crítica(s) sem mapeamento ({r['contrato']}/{r['controle']}, execução {r['execucao_id']})."})
    if r["correcoes"]:
        _cora("/notificar-aprovacao-ciclo", {
            "upn": resp["upn"], "execucao_id": r["execucao_id"], "contrato": r["contrato"], "controle": r["controle"],
            "rodada": r["rodada"], "contadores": r["contadores"], "correcoes": r["correcoes"],
            "responsavel": resp["nome"], "origem": contexto.get("origem"),
            "relatorio_base64": _arquivo_b64(r["relatorio"]),
            "relatorio_nome": f"Motor_{r['contrato']}_{r['controle']}_decisoes.xlsx"})


# ── aprovar / descartar ──────────────────────────────────────────────────────────────────────────
def _execucao(conn_s, eid: int) -> dict:
    with conn_s.cursor() as cur:
        cur.execute("""SELECT status, aprovador, codigo_contrato, numero_controle, contexto FROM saneamento.execucao
                        WHERE id=%s""", (eid,))
        row = cur.fetchone()
    if not row:
        raise Recusa(f"a análise {eid} não existe")
    return dict(zip(("status", "aprovador", "contrato", "controle", "contexto"), row))


def aprovar(eid: int, escopo: str, aprovado_por: str, sistema: bool = False) -> dict:
    """sistema=True (reenvio automático do motor, 15/10): quem já entrou no contrato / está em movimentação mais nova
    sai da carga em vez de travar tudo, e não há complemento (as sugestões já foram pro pós-vendas no arquivo do lote)."""
    conn_s = conectar_saneamento()
    try:
        e = _execucao(conn_s, eid)
        if not e["aprovador"] or aprovado_por.lower() != e["aprovador"].lower():
            raise Recusa("só o pós-vendas que pediu esta análise aprova o reenvio")
        if e["status"] != "CONCLUIDA":
            raise Recusa(f"essa análise já está {e['status'].lower()} — nada a fazer")
        with conn_s.cursor() as cur:
            cur.execute("""SELECT conteudo FROM saneamento.nova_carga WHERE execucao_id=%s AND escopo=%s
                            ORDER BY id DESC LIMIT 1""", (eid, escopo))
            row = cur.fetchone()
        if not row:
            raise Recusa(f"não há carga {escopo} nessa análise")
        linhas = [l for l in row[0].splitlines() if l]
        contrato = e["contrato"]
        ruins = linhas_que_derrubam("".join(l + "\n" for l in linhas).encode("latin-1"))
        if ruins:   # 17/10/2026: nunca sobe o que derruba o arquivo inteiro
            raise Recusa("o reenvio tem linha que a Hapvida recusaria inteira: " + "; ".join(ruins[:3]))
        # reconfere no portal: alguém da carga já está no contrato, num arquivo mais novo (mesmo processando) ou
        # num pedido via tela? Qualquer um -> não sobe. Consulta que falha -> não sobe.
        cpfs = {l[119:130].strip() for l in linhas}
        controles = (e["contexto"] or {}).get("controles_origem") or [e["controle"]]
        desde = max((json.loads((DADOS / contrato / str(c) / "movimento.json").read_text()).get("createdAt") or "")
                    for c in controles)
        try:
            with coleta_portal.sessao(contrato) as (page, headers):
                conf = coleta_portal.conferir_antes_de_reenviar(page, headers, contrato, cpfs, desde, controles)
        except coleta_portal.ConferenciaInvalida as erro:
            # 16/10/2026: nada subiu -> é seguro repetir; o cron do ciclo (retentar_reenvios) tenta de novo sozinho
            _marcar_reenvio_pendente(conn_s, eid, escopo, aprovado_por, sistema, str(erro))
            raise Recusa(f"não consegui conferir na Hapvida ({erro}) — sem conferência eu não subo nada. "
                         "Vou tentar de novo sozinho a cada 30 minutos.")
        partes = []
        if conf["no_contrato"]:
            partes.append(f"{len(conf['no_contrato'])} já aparece(m) no contrato")
        if conf["em_movimentacao"]:
            partes.append(f"{len(conf['em_movimentacao'])} já está(ão) em movimentação mais nova "
                          f"({', '.join(sorted(set(conf['em_movimentacao'].values())))})")
        if conf["via_tela"]:
            partes.append(f"{len(conf['via_tela'])} tem pedido feito pela tela")
        if partes and sistema:
            fora = set(conf["no_contrato"]) | set(conf["em_movimentacao"]) | set(conf["via_tela"])
            linhas = [l for l in linhas if l[119:130].strip() not in fora]
            linhas = [l[:0] + f"{i:07d}" + l[7:] for i, l in enumerate(linhas, 1)]
            if not linhas:
                persistencia.marcar_desfecho(conn_s, eid, "DESCARTADA", aprovado_por, {"motivo": "todos já no contrato"})
                return {"enviado": False, "status": None, "contrato": contrato, "linhas": 0, "motivo": "todos já no contrato"}
            partes = []
        if partes:
            raise Recusa("das pessoas da carga, " + "; ".join(partes) + ". Não subi nada para não duplicar — "
                         "analise a movimentação mais nova quando ela terminar de processar.")
        r = persistencia.aprovar(conn_s, eid, aprovado_por, viu_remocoes=True, escopo=escopo)
        if not r["ok"]:
            raise Recusa(r["erro"])
        nome = f"CICLO_{eid}_{contrato}.txt"
        caminho = Path(tempfile.mkdtemp()) / nome
        caminho.write_bytes("".join(l + "\n" for l in linhas).encode("ascii") if sistema else row[0].encode("ascii"))
        try:
            up = ms.run_inclusao(os.environ.get("GCP_PROJECT", ""), contrato, str(caminho))
        finally:
            caminho.unlink(missing_ok=True)
        status = up.get("status")
        if status != 200:
            # fica APROVADA sem ENVIADA: o portal não confirmou; conferir no portal antes de analisar de novo
            return {"enviado": False, "status": status, "contrato": contrato, "linhas": len(linhas)}
        confirmacao = ((up.get("body") or {}).get("content")) if isinstance(up.get("body"), dict) else None
        protocolo = _achar_protocolo(contrato, nome)
        persistencia.marcar_desfecho(conn_s, eid, "ENVIADA", aprovado_por,
                                     {"status_upload": status, "confirmacao": confirmacao, "protocolo": protocolo,
                                      "escopo": escopo, "linhas": len(linhas)})
        ctx = e["contexto"] or {}
        conn_l = _conn_leitura()
        try:
            _registrar_acompanhamento(conn_l, contrato, confirmacao, protocolo, nome, len(linhas), ctx, eid)
            comp = None if sistema else ciclo.complemento_para_responsavel(conn_l, conn_s, eid)
        finally:
            conn_l.close()
        if comp:
            _notificar_complemento(comp)
        return {"enviado": True, "status": status, "protocolo": protocolo, "contrato": contrato, "linhas": len(linhas),
                "complemento": comp["linhas"] if comp else 0, "responsavel": comp["responsavel"]["nome"] if comp else None}
    finally:
        conn_s.close()


MAX_RETENTATIVAS = 20   # ~10h de expediente com o cron de 30 em 30 min


def _marcar_reenvio_pendente(conn_s, eid: int, escopo: str, por: str, sistema: bool, erro: str) -> None:
    with conn_s.cursor() as cur:
        cur.execute("SELECT contexto -> 'reenvio_pendente' FROM saneamento.execucao WHERE id = %s", (eid,))
        ant = (cur.fetchone() or [None])[0] or {}
        pend = {"escopo": escopo, "por": por, "sistema": sistema, "tentativas": int(ant.get("tentativas") or 0) + 1,
                "ultimo_erro": erro[:300], "em": datetime.now().isoformat(timespec="seconds")}
        cur.execute("""UPDATE saneamento.execucao SET contexto = coalesce(contexto, '{}'::jsonb) || jsonb_build_object('reenvio_pendente', %s::jsonb)
                       WHERE id = %s""", (json.dumps(pend), eid))
    conn_s.commit()


def retentar_reenvios() -> list[dict]:
    """Reenvios que pararam porque o portal não deixou conferir (nada subiu). Tenta de novo; sucesso limpa a marca;
    depois de MAX_RETENTATIVAS avisa o André uma vez e para."""
    conn_s = conectar_saneamento()
    try:
        with conn_s.cursor() as cur:
            cur.execute("""SELECT id, contexto -> 'reenvio_pendente' FROM saneamento.execucao
                            WHERE status = 'CONCLUIDA' AND contexto ? 'reenvio_pendente' ORDER BY id""")
            pendentes = cur.fetchall()
    finally:
        conn_s.close()
    feitos = []
    for eid, pend in pendentes:
        if int(pend.get("tentativas") or 0) >= MAX_RETENTATIVAS:
            continue
        try:
            r = aprovar(eid, pend["escopo"], pend["por"], sistema=bool(pend.get("sistema")))
        except Recusa as erro:
            r = {"enviado": False, "erro": str(erro)}
        print("[reenvio] execução", eid, json.dumps(r, default=str)[:300])
        portal_fora = "não consegui conferir" in (r.get("erro") or "")
        if not portal_fora:   # subiu, ou recusa definitiva (já no contrato / duplicado / já enviada): não repete
            conn_s = conectar_saneamento()
            try:
                with conn_s, conn_s.cursor() as cur:
                    cur.execute("UPDATE saneamento.execucao SET contexto = contexto - 'reenvio_pendente' WHERE id = %s", (eid,))
            finally:
                conn_s.close()
        elif int(pend.get("tentativas") or 0) + 1 >= MAX_RETENTATIVAS:
            _cora("/notificar", {"upn": ciclo.ADMIN,
                                 "texto": f"⚠️ Reenvio da execução {eid} não subiu depois de {MAX_RETENTATIVAS} tentativas: "
                                          f"{r.get('erro') or 'portal não confirmou'}"})
        feitos.append({"execucao_id": eid, **r})
    return feitos


def descartar(eid: int, por: str) -> dict:
    conn_s = conectar_saneamento()
    try:
        e = _execucao(conn_s, eid)
        if not e["aprovador"] or por.lower() != e["aprovador"].lower():
            raise Recusa("só o pós-vendas que pediu esta análise decide o reenvio")
        if e["status"] != "CONCLUIDA":
            raise Recusa(f"essa análise já está {e['status'].lower()} — nada a fazer")
        persistencia.marcar_desfecho(conn_s, eid, "DESCARTADA", por, {})
        conn_l = _conn_leitura()
        try:
            comp = ciclo.complemento_para_responsavel(conn_l, conn_s, eid)
        finally:
            conn_l.close()
        if comp:
            _notificar_complemento(comp)
        return {"descartado": True, "complemento": comp["linhas"] if comp else 0,
                "responsavel": comp["responsavel"]["nome"] if comp else None}
    finally:
        conn_s.close()


def _notificar_complemento(comp: dict) -> None:
    _cora("/notificar-arquivo", {
        "upn": comp["responsavel"]["upn"],
        "texto": f"📋 **{comp['contrato']}, movimentação {comp['controle']}**: {comp['linhas']} linha(s) que o motor tinha "
                 "corrigido não foram reenviadas (o cartão não aprovou essas linhas). Estão no anexo, com a sugestão do motor — "
                 "confirme com o cliente e me devolva escrevendo **correção motor de críticas**.",
        "arquivo_base64": _arquivo_b64(comp["arquivo_responsavel"]),
        "nome_arquivo": Path(comp["arquivo_responsavel"]).name})


def _achar_protocolo(contrato: str, nome_arquivo: str, tentativas: int = 3) -> str | None:
    base = os.path.splitext(nome_arquivo)[0]
    for _ in range(tentativas):
        dados = ms.checar_status_movimentacoes(os.environ.get("GCP_PROJECT", ""), contrato, limit=10)
        for m in (dados.get("content") or {}).get("movements") or []:
            if (m.get("uploadName") or "").startswith(base):
                return str(m.get("controlCode"))
        time.sleep(20)
    return None


def _registrar_acompanhamento(conn_l, contrato, confirmacao, protocolo, nome, linhas, ctx, eid) -> None:
    """O reenvio entra no mesmo vigia (#154) com rodada+1 — 12h depois o ciclo roda de novo nele."""
    with conn_l, conn_l.cursor() as cur:
        cur.execute("""INSERT INTO movimentacoes_hapvida_acompanhamento
                         (codigo_contrato, confirmacao_upload, nome_arquivo, total_vidas, solicitante_upn,
                          solicitante_nome, proxima_checagem_em, rodada, execucao_origem_id, controle_portal)
                       VALUES (%s,%s,%s,%s,%s,%s, now() + interval '45 minutes', %s, %s, %s)""",
                    (contrato, confirmacao or protocolo, nome, linhas, ctx.get("solicitante") or ciclo.ADMIN,
                     "Motor de Críticas", int(ctx.get("rodada") or 1) + 1, eid, protocolo))


# ── gancho do vigia ──────────────────────────────────────────────────────────────────────────────
def do_acompanhamento(linha: dict, control_code) -> list[dict]:
    """Chamado pelo verificar_acompanhamentos.py quando a movimentação chega a status final."""
    tipo = "VIGIA_EXCLUSAO" if (linha.get("nome_arquivo") or "").upper().startswith("EXCLUSAO_") else "VIGIA_INCLUSAO"
    return rodar(linha["codigo_contrato"], [str(control_code)],
                 {"origem": tipo, "solicitante": linha.get("solicitante_upn"), "rodada": linha.get("rodada") or 1,
                  "acompanhamento_id": linha["id"]})


# ── volta da planilha de correção do pós-vendas (17/10/2026) ─────────────────────────────────────────
# André: "arquivos de correção vazios na aba Correções e com informação na aba Ação no portal que a Cora nem você
# acionam o MOTOR"; "não estamos usando nossa inteligência para aprender com as respostas". Antes, a volta era um
# caminho à parte (só aplicava o que a pessoa digitou na aba Correções). Agora: as respostas (as duas abas) viram
# FONTE do motor e ficam guardadas (na movimentação e por CPF); o motor roda de novo com coleta nova na Hapvida,
# reenvia sozinho o que ficou certo e devolve uma planilha só com o que ainda trava.
ARQ_MEMORIA = DADOS / ciclo.ARQ_MEMORIA


def _gravar_json(caminho: Path, dados) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1))
    os.chmod(tmp, 0o600)
    tmp.replace(caminho)


def guardar_respostas(res: dict, quem: str, arquivo: str) -> None:
    """Mescla as respostas no pos_vendas.json de cada movimentação e na memória por CPF (com trava: duas correções
    ao mesmo tempo não podem perder resposta — achado da mesa de arquitetura, 17/10)."""
    import fcntl
    with open(DADOS / ".respostas.lock", "w") as trava:
        fcntl.flock(trava, fcntl.LOCK_EX)
        _guardar_respostas(res, quem, arquivo)


def _guardar_respostas(res: dict, quem: str, arquivo: str) -> None:
    agora = datetime.now().astimezone().isoformat(timespec="seconds")
    memoria = json.loads(ARQ_MEMORIA.read_text()) if ARQ_MEMORIA.exists() else {}
    for (contrato, mov), regs in res["respostas"].items():
        pasta = DADOS / contrato / mov
        f = pasta / ciclo.ARQ_RESPOSTAS
        atual = json.loads(f.read_text()) if f.exists() else {"respostas": {}, "historico": []}
        originais = {l[:7]: l for l in (pasta / "original.txt").read_bytes().decode("latin-1").splitlines()}
        for seq, r in regs.items():
            a = atual["respostas"].setdefault(seq, {"campos": {}, "confirmados": [], "feito": False})
            a["campos"].update(r["campos"])
            a["confirmados"] = sorted(set(a["confirmados"]) | set(r["confirmados"]))
            a["feito"] = a["feito"] or r["feito"]
            a["remover"] = a.get("remover", False) or r.get("remover", False)
            linha = originais.get(seq, "")
            for campo, v in a["campos"].items():
                linha = layout.gravar(linha, campo, v)
            cpf = layout.ler(linha, "108") if linha else ""
            if cpf:
                # 17/10/2026 (mesa LGPD): só dado de identificação/endereço da pessoa (minimização), com origem e data;
                # vale 12 meses (ciclo.ler_memoria ignora o vencido)
                m = memoria.setdefault(cpf, {})
                for campo in list(r["campos"]) + list(r["confirmados"]):
                    if campo in ciclo.CAMPOS_MEMORIA:
                        m[campo] = {"valor": layout.ler(linha, campo), "contrato": contrato, "movimentacao": mov,
                                    "em": agora, "quem": quem}
        atual["historico"].append({"em": agora, "quem": quem, "arquivo": arquivo, "linhas": sorted(regs)})
        _gravar_json(f, atual)
    _gravar_json(ARQ_MEMORIA, memoria)


def _ja_recebida(sha: str):
    conn_s = conectar_saneamento()
    try:
        with conn_s.cursor() as cur:
            cur.execute("""SELECT s.criado_em FROM saneamento.solicitacao s JOIN saneamento.interacao i
                             ON i.solicitacao_id = s.id AND i.tipo = 'CORRECAO_RECEBIDA'
                            WHERE i.conteudo->>'sha256' = %s ORDER BY s.id LIMIT 1""", (sha,))
            row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn_s.close()


def _registrar_solicitacao(quem: str, arquivo: str, sha: str, res: dict) -> int:
    from psycopg2.extras import Json
    conn_s = conectar_saneamento()
    try:
        with conn_s, conn_s.cursor() as cur:
            contratos = ",".join(sorted({c for c, _ in res["respostas"]}))[:20]
            cur.execute("""INSERT INTO saneamento.solicitacao (solicitante, canal, canal_ref, codigo_contrato, arquivo_nome,
                             status) VALUES (%s, 'TEAMS', %s, %s, %s, 'AGUARDANDO_POS_VENDAS') RETURNING id""",
                        (quem, Json({}), contratos, arquivo))
            sid = cur.fetchone()[0]
            cur.execute("""INSERT INTO saneamento.interacao (solicitacao_id, direcao, canal, tipo, ator, conteudo)
                           VALUES (%s, 'ENTRADA', 'TEAMS', 'CORRECAO_RECEBIDA', %s, %s)""",
                        (sid, quem, Json({"arquivo": arquivo, "sha256": sha, "linhas": res["linhas"],
                                          "respostas": {f"{c}|{m}": v for (c, m), v in res["respostas"].items()}})))
        return sid
    finally:
        conn_s.close()


def receber_correcao(conteudo: bytes, arquivo: str, quem: str) -> dict:
    """Parte rápida (a Cora responde na hora): lê, confere, guarda. -> {"ok", "mensagem", "trabalho"?}"""
    import hashlib
    sha = hashlib.sha256(conteudo).hexdigest()
    quando = _ja_recebida(sha)
    if quando:
        return {"ok": False, "mensagem": f"Este arquivo é igual ao que chegou às {quando:%H:%M} de {quando:%d/%m} — nada novo. "
                                         "Se corrigiu algo, saia da célula (para o Excel salvar) e mande de novo."}
    res = correcao.respostas_da_planilha(conteudo, DADOS)
    if not res["ok"]:
        return {"ok": False, "mensagem": "⚠️ " + res["erro"]}
    if not res["linhas"]:
        return {"ok": False, "mensagem": "⚠️ Não achei nenhuma linha do motor nesse arquivo (coluna ID_MOTOR vazia)."}
    guardar_respostas(res, quem, arquivo)
    sid = _registrar_solicitacao(quem, arquivo, sha, res)
    return {"ok": True, "solicitacao_id": sid, "linhas": res["linhas"], "erros": res["erros"],
            "trabalho": {c: {m: seqs for (cc, m), seqs in res["escopo"].items() if cc == c}
                         for c in sorted({c for c, _ in res["escopo"]})},
            "mensagem": f"Recebi a correção: {res['linhas']} pessoa(s). Rodo o motor com os dados de hoje da Hapvida e te "
                        "aviso o resultado aqui."}


def processar_correcao(recebida: dict, arquivo: str, quem: str, nome: str | None = None) -> dict:
    """Parte demorada (segundo plano): motor em cada movimentação, reenvio automático, uma mensagem com o resultado."""
    sid = recebida["solicitacao_id"]
    enviados, falhas, corrigir, portal, resolvidas = [], [], [], [], 0
    for contrato, por_mov in recebida["trabalho"].items():
        ctx = {"origem": "CORRECAO_POS_VENDAS", "solicitante": quem, "rodada": 2, "escopo_por_mov": por_mov,
               "solicitacao_id": sid}
        try:
            resumos = rodar(contrato, sorted(por_mov), ctx, notificar=False)
        except Recusa:
            # uma movimentação que não dá pra analisar (ex.: recusada inteira, "não iniciou") não pode travar as outras
            resumos = []
            for mov in sorted(por_mov):
                try:
                    resumos += rodar(contrato, [mov], ctx, notificar=False)
                except Exception as e:
                    falhas.append(f"{contrato} movimentação {mov}: {e}")
        except Exception as e:
            falhas.append(f"{contrato}: {e}")
            continue
        for r in resumos:
            corrigir += r.get("itens_corrigir") or []
            portal += r.get("itens_portal") or []
            resolvidas += len(r.get("resolvidas") or [])
            env = r.get("reenvio") or {}
            if env.get("enviado"):
                enviados.append(env)
            elif r.get("reenvio_automatico"):
                falhas.append(f"{contrato}: {env.get('erro') or env.get('motivo') or 'o portal não confirmou o upload'}")
    vidas = sum(int(e.get("linhas") or 0) for e in enviados)
    partes = [f"📥 **Correção {arquivo}**"]
    if enviados:
        prot = ", ".join(f"{e['contrato']} movimentação {e['protocolo']}" if e.get("protocolo") else
                         f"{e['contrato']} (protocolo ainda não aparece no portal — confiro em 45 min)" for e in enviados)
        partes.append(f"✅ {vidas} vida(s) reenviada(s) à Hapvida: {prot}.")
    if resolvidas - vidas > 0:
        partes.append(f"✔️ {resolvidas - vidas} já resolvida(s) sem reenvio (já ativas ou em outro pedido).")
    for f in falhas:
        partes.append(f"⚠️ {f}")
    anexo = None
    if corrigir or portal:
        titulo = f"{(nome or quem).split()[0].title()} — correção {sid}"
        conteudo = ciclo.planilha_responsavel(corrigir, portal, titulo)
        contratos = "_".join(sorted({i["contrato"] for i in corrigir + portal}))
        anexo = DADOS / "_saidas" / "correcoes" / str(sid) / f"CORRECAO_MOTOR_{contratos}_{date.today():%Y%m%d}_{sid}.xlsx"
        anexo.parent.mkdir(parents=True, exist_ok=True)
        anexo.write_bytes(conteudo)
        os.chmod(anexo, 0o600)
        partes.append(f"⏳ {len(corrigir) + len(portal)} ainda trava(m): corrija só o vermelho (ou faça a ação no portal "
                      "e escreva SIM em Feito?) e devolva.")
    elif not falhas:
        partes.append("Nada mais pendente nesta planilha.")
    texto = "\n".join(partes)
    if anexo:
        _cora("/notificar-arquivo", {"upn": quem, "texto": texto, "arquivo_base64": _arquivo_b64(str(anexo)),
                                     "nome_arquivo": anexo.name})
    else:
        _cora("/notificar", {"upn": quem, "texto": texto})
    if quem.lower() != ciclo.ADMIN.lower():
        _cora("/notificar", {"upn": ciclo.ADMIN, "texto": f"(cópia) {nome or quem}: " + texto})
    return {"enviados": enviados, "pendentes": len(corrigir) + len(portal), "falhas": falhas}
