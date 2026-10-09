"""Ciclo de críticas — lado do portal (pedido do André, 08/10/2026).

O motor mora no repo lifetools (`service/saneamento/`, montado só-leitura em /lifetools_service). Aqui só
o que precisa do portal ou da Cora: coletar a movimentação, chamar o ciclo, avisar quem precisa e, depois
do clique do pós-vendas responsável pelo contrato, subir o reenvio.

Quem dispara:
  - verificar_acompanhamentos.py (vigia #154/#156): movimentação da Cora chegou a status final;
  - a Cora, menu "Analisar críticas": POST /ciclo/movimentacoes (lista do dia) e /ciclo/rodar;
  - a Cora, cartão de aprovação: POST /ciclo/aprovar e /ciclo/descartar.

Regras (decisões do André, 08-09/10):
  - reenvio só com APROVAÇÃO EM 1 CLIQUE do pós-vendas responsável pelo contrato (empresa_responsavel; sem
    cadastro, o André) — "ele que vai aprovar e você executar" (09/10); antes de subir, reconfere no portal se alguém da carga
    já ficou ativo desde a análise (se sim, não sobe e pede nova análise);
  - o que o motor não resolve vai pro responsável interno (empresa_responsavel), nunca pro cliente;
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
from saneamento import ciclo, coleta_portal, persistencia  # noqa: E402
from saneamento.catalogo import classificar  # noqa: E402
from saneamento.cli import conectar as conectar_saneamento  # noqa: E402
from saneamento.ferramentas import coletar_ceps  # noqa: E402

DADOS = Path(os.environ.get("CICLO_DADOS", "/dados_motor"))
CORA = os.environ.get("CORA_BASE_URL", "http://cora:3978")
STATUS_FINAIS = {"FileProcessed", "FileProcessedWithReview", "ProcessFileWithPendency"}
STATUS_PT = {"FileProcessed": "Processado sem críticas", "FileProcessedWithReview": "Finalizado com críticas",
             "ProcessFileWithPendency": "Processamento com pendência", "ProcessFile": "Em processamento",
             "UnableToStart": "Ainda não iniciou"}


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
        resumos = [ciclo.executar_ciclo(str(p), conn_l, conn_s, contexto) for _, p in coletadas]
        resumos += [_ciclo_exclusao(contrato, cc, p, conn_l) for cc, p in exclusoes]
    finally:
        conn_l.close()
        conn_s.close()
    for r in resumos:
        print("[ciclo]", ciclo.resumo_json(r) if "execucao_id" in r else json.dumps({k: r[k] for k in ("contrato", "controle", "tipo")}))
        if notificar:
            _notificar(r, contexto)
    return resumos


def _ciclo_exclusao(contrato: str, controle: str, pasta: Path, conn_l) -> dict:
    vidas = json.loads((pasta / "vidas.json").read_text())
    itens = []
    for v in vidas:
        if v.get("status") == "Processed":
            continue
        textos = [r if isinstance(r, str) else (r.get("description") or r.get("message") or "") for r in v.get("reviews") or []]
        cods = [classificar(t, "VIDA").codigo for t in textos] or ["SEM_CRITICA"]
        itens.append({"linha": str(v.get("sequentialNumber")).zfill(7), "nome": (v.get("life") or {}).get("name"),
                      "status": v.get("status"), "criticas": " | ".join(textos), "categorias": ", ".join(cods)})
    resp = ciclo.responsavel_interno(conn_l, contrato)
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
    texto = _texto_resumo(r)
    resp = r["responsavel"]
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


def aprovar(eid: int, escopo: str, aprovado_por: str) -> dict:
    conn_s = conectar_saneamento()
    try:
        e = _execucao(conn_s, eid)
        if not e["aprovador"] or aprovado_por.lower() != e["aprovador"].lower():
            raise Recusa("só o responsável do contrato aprova o reenvio do motor")
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
        # reconfere no portal: alguém da carga já está no contrato, num arquivo mais novo (mesmo processando) ou
        # num pedido via tela? Qualquer um -> não sobe. Consulta que falha -> não sobe.
        cpfs = {l[119:130].strip() for l in linhas}
        with conn_s.cursor() as cur:
            cur.execute("SELECT entrada_ref FROM saneamento.execucao WHERE id=%s", (eid,))
            pasta = Path(cur.fetchone()[0])
        desde = json.loads((pasta / "movimento.json").read_text()).get("createdAt") or ""
        try:
            with coleta_portal.sessao(contrato) as (page, headers):
                conf = coleta_portal.conferir_antes_de_reenviar(page, headers, contrato, cpfs, desde, e["controle"])
        except coleta_portal.ConferenciaInvalida as erro:
            raise Recusa(f"não consegui conferir na Hapvida ({erro}) — sem conferência eu não subo nada. Tente de novo mais tarde.")
        partes = []
        if conf["no_contrato"]:
            partes.append(f"{len(conf['no_contrato'])} já aparece(m) no contrato")
        if conf["em_movimentacao"]:
            partes.append(f"{len(conf['em_movimentacao'])} já está(ão) em movimentação mais nova "
                          f"({', '.join(sorted(set(conf['em_movimentacao'].values())))})")
        if conf["via_tela"]:
            partes.append(f"{len(conf['via_tela'])} tem pedido feito pela tela")
        if partes:
            raise Recusa("das pessoas da carga, " + "; ".join(partes) + ". Não subi nada para não duplicar — "
                         "analise a movimentação mais nova quando ela terminar de processar.")
        r = persistencia.aprovar(conn_s, eid, aprovado_por, viu_remocoes=True, escopo=escopo)
        if not r["ok"]:
            raise Recusa(r["erro"])
        nome = f"CICLO_{eid}_{contrato}.txt"
        caminho = Path(tempfile.mkdtemp()) / nome
        caminho.write_bytes(row[0].encode("ascii"))
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
            comp = ciclo.complemento_para_responsavel(conn_l, conn_s, eid)
        finally:
            conn_l.close()
        if comp:
            _notificar_complemento(comp)
        return {"enviado": True, "status": status, "protocolo": protocolo, "contrato": contrato, "linhas": len(linhas),
                "complemento": comp["linhas"] if comp else 0, "responsavel": comp["responsavel"]["nome"] if comp else None}
    finally:
        conn_s.close()


def descartar(eid: int, por: str) -> dict:
    conn_s = conectar_saneamento()
    try:
        e = _execucao(conn_s, eid)
        if not e["aprovador"] or por.lower() != e["aprovador"].lower():
            raise Recusa("só o responsável do contrato decide o reenvio do motor")
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
