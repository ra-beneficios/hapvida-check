"""
#154/#156 - Monitoramento pos-upload das movimentacoes Hapvida (pedido do Andre, 04/09 e 14/09).

Fluxo:
  1. Cora grava uma linha em movimentacoes_hapvida_acompanhamento logo depois de um upload com
     200 OK (status inicial AGUARDANDO_CHECAGEM_INICIAL, proxima_checagem_em = +45min).
  2. Esse script roda de tempos em tempos (cron) e processa linhas com proxima_checagem_em
     vencida:
       - AGUARDANDO_CHECAGEM_INICIAL: confere se a movimentacao aparece na lista real do
         portal (API movements-list). Se aparece com qualquer status -> avisa o solicitante e
         decide: se ja e' um status final (ex: "Finalizado com criticas"), fecha (CONCLUIDO); se
         nao, avisa que esta "Em movimentacao" e agenda a checagem de 12h (#156, era 24h). Se
         NAO aparece depois de 45min (achado real: checar cedo demais e enganoso, mas 45min ja
         e' tempo suficiente pra pelo menos REGISTRAR na lista, mesmo que o processamento em si
         demore mais) -> marca FALHOU e avisa imediatamente.
       - AGUARDANDO_CHECAGEM_RELATORIO (12h): busca o relatorio de VERDADE (nome/CPF/motivo de
         cada vida criticada ou cancelada, via lifes-with-summary - ver #155) da movimentacao
         especifica e manda pro solicitante. Marca CONCLUIDO.

Por que 45min e nao "imediatamente" (like o Andre pediu): ja documentamos (150_procedimento_
inclusao_hapvida_protocolo.md) que status logo apos o upload e' sempre enganoso ("UnableToStart"
mesmo em uploads que depois deram certo). 45min e' o meio-termo: cedo o bastante pra pegar erro
real rapido, tarde o bastante pra nao alarmar por engano.

Roda dentro do container hapvida-check (Playwright + GCP Secret Manager ja configurados).
"""
import os
import sys
import json
import datetime

import psycopg2
import psycopg2.extras
import requests

sys.path.append("/app")
import movimentacao_saude as ms

DB = dict(
    host=os.getenv("DB_HOST", "postgres"),
    user=os.getenv("DB_USER", "radb"),
    password=os.environ.get("DB_PASSWORD", ""),
    dbname=os.getenv("DB_NAME", "lifetools"),
)

CORA_NOTIFICAR_URL = os.environ.get("CORA_NOTIFICAR_URL", "http://cora:3978/notificar")
CORA_NOTIFICAR_ARQUIVO_URL = os.environ.get("CORA_NOTIFICAR_ARQUIVO_URL", "http://cora:3978/notificar-arquivo")
CORA_LEMBRAR_ONDA_URL = os.environ.get("CORA_LEMBRAR_ONDA_URL", "http://cora:3978/exclusao/lembrar-onda")
ADMIN_UPN = os.environ.get("ADMIN_UPN", "andre.aguiar@ra-beneficios.com.br")
# Lembrete da onda pendente (13/10, C-MMIND): 1o cartao assim que os titulares terminam, depois a cada 24h;
# do 2o lembrete em diante o Andre tambem fica sabendo. Lote com titulares concluidos ha mais de 30 dias sai.
LEMBRETE_INTERVALO_H = 24
LEMBRETE_JANELA_DIAS = 30
API_URL = os.environ.get("CONVERTER_ENDPOINT", "http://lifetools-api-1:8000")

STATUS_PT = {
    "FileProcessed": "Processado sem críticas",
    "FileProcessedWithReview": "Finalizado com críticas",
    "ProcessFileWithPendency": "Processamento com pendência",
    "UnableToStart": "Ainda não iniciou o processamento (transitório)",
    "ProcessFile": "Em processamento",
}
# Lista positiva (nao negativa) de proposito: so trata como "resultado final" um status ja
# CONFIRMADO como definitivo em movimentacoes antigas (>24h) - qualquer status novo/desconhecido
# (achado real, 04/09: "ProcessFile" apareceu e nao estava documentado) cai no default seguro
# de "ainda nao e final", igual "UnableToStart" - nunca declara sucesso/fim cedo demais.
# "FileProcessed" (processado sem nenhuma critica) apareceu na 1a exclusao real (protocolo 1330271, 02/10) - sem
# ele aqui, um envio 100% limpo ficava "em andamento" para sempre.
STATUS_FINAIS_CONHECIDOS = {"FileProcessed", "FileProcessedWithReview", "ProcessFileWithPendency"}
STATUS_COM_CRITICA = {"FileProcessedWithReview", "ProcessFileWithPendency"}
# Ciclo de criticas (08/10/2026): com a chave ligada, movimentacao final com critica vai pro Motor de Criticas
# (resolve o que der, reenvio so' com aprovacao do Andre, resto pro responsavel) em vez do relatorio cru.
CICLO_NO_VIGIA = os.environ.get("CICLO_NO_VIGIA") == "1"
# 12h depois ainda processando: reagenda de 2 em 2h ate 36h do envio; depois disso segue com o que tiver
REAGENDAR_ATE_HORAS = 36


def _rodar_ciclo(cur, linha: dict, control_code) -> bool:
    """True se o ciclo tratou (e ja avisou todo mundo); False -> segue o fluxo antigo."""
    if not CICLO_NO_VIGIA:
        return False
    try:
        import ciclo_criticas
        resumos = ciclo_criticas.do_acompanhamento(linha, control_code)
        eid = next((r.get("execucao_id") for r in resumos if r.get("execucao_id")), None)
        cur.execute("UPDATE movimentacoes_hapvida_acompanhamento SET ciclo_execucao_id=%s, controle_portal=%s WHERE id=%s",
                    (eid, str(control_code), linha["id"]))
        return True
    except Exception as e:
        print(f"[{linha['id']}] ciclo de criticas falhou, seguindo com o relatorio antigo: {type(e).__name__}: {e}")
        return False


def _datas_exclusao(linha: dict) -> str:
    """Bloco "você pediu x a Hapvida registrou" (13/10). Best-effort: nunca derruba nem atrasa o aviso."""
    nome = linha.get("nome_arquivo") or ""
    if not nome.upper().startswith("EXCLUSAO_"):
        return ""
    try:
        import exclusao_api
        return exclusao_api.datas_do_envio(nome)
    except Exception as e:
        print(f"[{linha['id']}] nao consegui comparar as datas da exclusao: {e}")
        return ""


def _reagendar_por_entrega(cur, linha: dict) -> None:
    """13/10: o resultado final so' conta como entregue se a Cora confirmou. Senao volta pra fila em 15 min (o
    relatorio e' refeito e reenviado); antes o status virava CONCLUIDO e o aviso perdido nunca mais era tentado."""
    print(f"[{linha['id']}] notificacao final NAO entregue - nova tentativa em 15 min")
    cur.execute("""UPDATE movimentacoes_hapvida_acompanhamento
                   SET status = 'AGUARDANDO_CHECAGEM_RELATORIO', notificado_em = NULL,
                       proxima_checagem_em = now() + interval '15 minutes',
                       detalhe_status = 'entrega da notificacao falhou - nova tentativa'
                   WHERE id = %s""", (linha["id"],))


def lembrar_ondas_pendentes(cur, agora: datetime.datetime | None = None, simular: bool = False) -> list[str]:
    """Exclusao: onda dos dependentes aprovada e NAO enviada, com a onda dos titulares do mesmo contrato ja
    processada -> manda o cartao com o botao "Enviar onda 2" de novo pra quem pediu (o clique continua sendo dele).
    Primeiro lembrete assim que os titulares terminam; depois a cada LEMBRETE_INTERVALO_H; do 2o em diante avisa
    o Andre tambem. Nunca derruba o acompanhamento."""
    agora = agora or datetime.datetime.now().astimezone()
    feitos: list[str] = []
    try:
        import exclusao_api
        servico, pastas = exclusao_api.servico, exclusao_api._pastas()
        lotes = sorted(os.listdir(pastas.lotes))
    except Exception as e:
        print(f"[lembrete-onda] sem acesso aos lotes de exclusao: {e}")
        return feitos
    for lote_id in lotes:
        try:
            estado = servico.ler_estado(lote_id, pastas)
        except Exception:
            continue
        if not estado.get("aprovacoes") or not estado.get("envios"):
            continue
        aprovados = estado["aprovacoes"][-1].get("arquivos") or []
        enviados = {e["hash"] for e in estado["envios"]}
        for dep in (a for a in aprovados if a["onda"] == "dependentes" and a["hash"] not in enviados):
            tit = next((e for e in estado["envios"] if e["onda"] == "titulares" and e["contrato"] == dep["contrato"]), None)
            if not tit:
                continue
            cur.execute("""SELECT status, notificado_em FROM movimentacoes_hapvida_acompanhamento
                           WHERE nome_arquivo = %s ORDER BY id DESC LIMIT 1""", (tit["arquivo"],))
            acomp = cur.fetchone()
            if not acomp or acomp["status"] != "CONCLUIDO" or not acomp["notificado_em"]:
                continue  # titulares ainda processando (ou aviso final ainda nao entregue)
            if agora - acomp["notificado_em"] > datetime.timedelta(days=LEMBRETE_JANELA_DIAS):
                continue
            lembretes = [l for l in estado.get("lembretes", []) if l.get("hash") == dep["hash"]]
            if lembretes and agora - datetime.datetime.fromisoformat(lembretes[-1]["em"]) < datetime.timedelta(hours=LEMBRETE_INTERVALO_H):
                continue
            dias = (agora - acomp["notificado_em"]).days
            texto = (f"⏳ **Exclusão {dep['contrato']}: falta enviar a onda 2 ({dep['linhas']} dependentes).**\n"
                     f"- Titulares: já processados pela Hapvida" + (f" há {dias} dia(s)" if dias else "") + "\n"
                     f"- Dependentes: continuam **ativos** e entram na próxima fatura até serem enviados\n\n"
                     f"Já está aprovado — é só clicar abaixo.")
            if simular:
                feitos.append(f"SIMULACAO {lote_id} {dep['contrato']} -> {estado['solicitante_upn']}")
                continue
            arquivo = {k: dep[k] for k in ("contrato", "onda", "linhas", "hash", "arquivo")} | {"vidas": []}
            try:
                r = requests.post(CORA_LEMBRAR_ONDA_URL, timeout=30,
                                  json={"upn": estado["solicitante_upn"], "lote": lote_id, "arquivos": [arquivo], "texto": texto})
                entregue = r.ok and r.json().get("enviado") is True
            except Exception as e:
                print(f"[lembrete-onda] {lote_id}: falha chamando a Cora: {e}")
                entregue = False
            if not entregue:
                print(f"[lembrete-onda] {lote_id}: lembrete NAO entregue - tenta de novo na proxima rodada")
                continue
            estado.setdefault("lembretes", []).append({"hash": dep["hash"], "em": agora.isoformat(), "para": estado["solicitante_upn"]})
            servico._salvar_estado(estado, pastas)
            if len(lembretes) >= 1:
                notificar(ADMIN_UPN, f"⏳ Exclusão {dep['contrato']} (lote `{lote_id}`): a onda 2 ({dep['linhas']} dependentes) "
                                     f"segue sem envio — {len(lembretes) + 1}º lembrete para {estado['solicitante_upn']}.")
            feitos.append(f"{lote_id} {dep['contrato']} -> {estado['solicitante_upn']} ({len(lembretes) + 1}º)")
    for f in feitos:
        print(f"[lembrete-onda] {f}")
    return feitos


def notificar(upn: str, texto: str) -> bool:
    if not upn:
        return False
    try:
        resp = requests.post(CORA_NOTIFICAR_URL, json={"upn": upn, "texto": texto}, timeout=15)
        # 13/10: 200 nao basta - a Cora responde {"enviado": false} quando a DM nao saiu
        return resp.ok and resp.json().get("enviado") is True
    except Exception as e:
        print(f"[notificar] falha ao chamar Cora: {e}")
        return False


def notificar_com_relatorio(
    codigo_contrato: str, control_code, vidas: list[dict], upn: str, texto: str
) -> bool:
    """#156 - gera o XLSX estruturado (categoria/prioridade/ação) na API e manda pro
    solicitante via Cora, em vez do texto corrido que ia direto no Teams (achado real,
    feedback do André: Raphael recebeu 31 críticas em texto aberto, ilegível)."""
    try:
        resp_xlsx = requests.post(
            f"{API_URL}/gerar-relatorio-criticas",
            json={"codigo_contrato": codigo_contrato, "movimentacao": control_code, "vidas": vidas},
            timeout=30,
        )
        if not resp_xlsx.ok:
            print(f"[notificar_com_relatorio] falha ao gerar xlsx: {resp_xlsx.status_code} {resp_xlsx.text[:300]}")
            return False
        xlsx_base64 = resp_xlsx.json()["xlsx_base64"]

        resp_cora = requests.post(
            CORA_NOTIFICAR_ARQUIVO_URL,
            json={
                "upn": upn,
                "texto": texto,
                "arquivo_base64": xlsx_base64,
                "nome_arquivo": f"Criticas_{codigo_contrato}_{control_code}.xlsx",
            },
            timeout=15,
        )
        return resp_cora.ok and resp_cora.json().get("enviado") is True
    except Exception as e:
        print(f"[notificar_com_relatorio] falha: {e}")
        return False


def achar_movimento(dados: dict, enviado_em) -> dict | None:
    """Acha a movimentacao correspondente ao que a Cora enviou. NAO da pra casar por nome de
    arquivo (achado real, 04/09): o app.py sobe um arquivo TEMPORARIO ("tmpXXXXX.txt"), nao o
    nome_arquivo salvo em nome_arquivo/saida_hapvida_*.txt - o nome real que aparece na API e'
    diferente. Casa pela movimentacao criada mais perto (e depois) do horario de envio, dentro
    de uma janela de tolerancia - createdAt e' registrado no upload, praticamente no mesmo
    instante que a Cora recebeu o 200 OK."""
    movimentos = (dados.get("content") or {}).get("movements") or []
    candidatos = []
    for m in movimentos:
        created_raw = m.get("createdAt")
        if not created_raw:
            continue
        try:
            created = datetime.datetime.fromisoformat(created_raw)
        except ValueError:
            continue
        diferenca = (created - enviado_em).total_seconds()
        # aceita ate 5 min ANTES (relogio ligeiramente dessincronizado) e 10 min DEPOIS
        if -300 <= diferenca <= 600:
            candidatos.append((abs(diferenca), m))
    if not candidatos:
        return None
    candidatos.sort(key=lambda par: par[0])
    return candidatos[0][1]


def _registrar_recusas_exclusao(linha: dict, codigo_contrato: str, control_code, vidas: list | None) -> str:
    """Motor de Exclusao (03/10): ao terminar, grava no lote quais vidas a Hapvida recusou - elas voltam a poder ser
    tratadas. Nunca derruba o acompanhamento. -> texto extra para a notificacao ("" se nada a dizer)."""
    nome_arquivo = linha.get("nome_arquivo") or ""
    if not nome_arquivo.upper().startswith("EXCLUSAO_"):
        return ""
    try:
        if vidas is None:
            vidas = ms.vidas_criticadas_de_movimentacao(os.environ["GCP_PROJECT"], codigo_contrato, control_code).get(
                "vidasCriticadas") or []
        import exclusao_api
        res = exclusao_api.registrar_recusas(nome_arquivo, vidas)
        if res and res["recusados"]:
            return (f"\n\n🔁 {len(res['recusados'])} vida(s) recusada(s) ficaram registradas no lote `{res['lote']}` e "
                    f"voltam a poder ser tratadas: " + ", ".join(r["codigo"] for r in res["recusados"]) + ".")
    except Exception as e:
        print(f"[{linha['id']}] erro registrando recusas da exclusao: {e}")
    return ""


def processar_linha(cur, linha: dict) -> None:
    codigo_contrato = linha["codigo_contrato"]
    confirmacao = linha["confirmacao_upload"]
    solicitante_upn = linha["solicitante_upn"]
    status_atual = linha["status"]
    project = os.environ["GCP_PROJECT"]

    try:
        dados = ms.checar_status_movimentacoes(project, codigo_contrato, limit=15)
    except Exception as e:
        print(f"[{linha['id']}] erro checando status: {e}")
        return

    # enviado_em vem do Postgres com tzinfo America/Sao_Paulo (-03:00) - createdAt da API vem
    # sem tzinfo mas bate com o horario local (BRT) mostrado na tela do portal - compara os
    # dois como horario local "naive", sem timezone.
    enviado_em_naive = linha["enviado_em"].replace(tzinfo=None)
    movimento = achar_movimento(dados, enviado_em_naive)

    # #154 - achado real (feedback do Andre, 04/09): o nome_arquivo que a Cora salva
    # ("saida_hapvida_FHMWR.txt") NUNCA e' o nome que aparece no portal - o app.py sobe um
    # arquivo temporario ("tmpXXXXX.txt"), o portal so conhece esse nome. Usar o "uploadName"
    # que veio da API (nome real, do movimento encontrado) evita apontar pra um arquivo que
    # não existe do ponto de vista de quem for conferir no portal.
    nome_no_portal = movimento.get("uploadName") if movimento else None
    identificador = f"contrato **{codigo_contrato}**, confirmação `{confirmacao or 'sem id'}`" + (
        f" (arquivo `{nome_no_portal}` no portal)" if nome_no_portal else ""
    )

    # Motor de Exclusao (03/10): o mesmo acompanhamento serve para exclusao - o nome do arquivo diz o tipo.
    nome_arquivo = linha.get("nome_arquivo") or ""
    tipo = "exclusão" if nome_arquivo.upper().startswith("EXCLUSAO_") else "inclusão"
    proxima_onda = (
        "\n\nA onda dos **titulares** terminou — agora você já pode clicar em **Enviar onda 2 (dependentes)** "
        "no cartão da exclusão."
        if "_titulares_" in nome_arquivo else ""
    )

    if status_atual == "AGUARDANDO_CHECAGEM_INICIAL":
        if not movimento:
            cur.execute(
                """UPDATE movimentacoes_hapvida_acompanhamento
                   SET status = 'FALHOU', ultima_checagem_em = now(), notificado_em = now(),
                       detalhe_status = 'nao encontrado na lista de movimentacoes apos 45min'
                   WHERE id = %s""",
                (linha["id"],),
            )
            notificar(
                solicitante_upn,
                f"⚠️ A {tipo} do {identificador} não apareceu na lista de movimentações do "
                f"portal Hapvida depois de 45 minutos — algo deu errado no upload. Precisa "
                f"verificar direto no portal ou tentar reenviar.",
            )
            return

        status_raw = movimento.get("status", "")
        status_legivel = STATUS_PT.get(status_raw, status_raw)
        eh_final = status_raw in STATUS_FINAIS_CONHECIDOS

        if eh_final:
            cur.execute(
                """UPDATE movimentacoes_hapvida_acompanhamento
                   SET status = 'CONCLUIDO', ultima_checagem_em = now(), notificado_em = now(),
                       detalhe_status = %s
                   WHERE id = %s""",
                (status_raw, linha["id"]),
            )
            extra_recusas = _registrar_recusas_exclusao(linha, codigo_contrato, movimento.get("controlCode"), None)
            if status_raw in STATUS_COM_CRITICA and _rodar_ciclo(cur, linha, movimento.get("controlCode")):
                return
            if not notificar(
                solicitante_upn,
                f"✅ A {tipo} do {identificador} já processou no portal Hapvida — status: "
                f"**{status_legivel}**. Se precisar do relatório detalhado de críticas, me avisa "
                f"que eu busco." + extra_recusas + _datas_exclusao(linha) + proxima_onda,
            ):
                _reagendar_por_entrega(cur, linha)
        else:
            cur.execute(
                """UPDATE movimentacoes_hapvida_acompanhamento
                   SET status = 'AGUARDANDO_CHECAGEM_RELATORIO', ultima_checagem_em = now(),
                       notificado_em = now(), detalhe_status = %s,
                       proxima_checagem_em = enviado_em + interval '12 hours'
                   WHERE id = %s""",
                (status_raw, linha["id"]),
            )
            notificar(
                solicitante_upn,
                f"✅ A {tipo} do {identificador} subiu com sucesso e está **em movimentação** "
                f"no portal Hapvida. Em até 12h eu confiro de novo e te mando o relatório de "
                f"críticas, se houver.",
            )

    elif status_atual == "AGUARDANDO_CHECAGEM_RELATORIO":
        if not movimento:
            cur.execute(
                """UPDATE movimentacoes_hapvida_acompanhamento
                   SET status = 'FALHOU', ultima_checagem_em = now(), notificado_em = now(),
                       detalhe_status = 'nao encontrado na lista de movimentacoes apos 12h'
                   WHERE id = %s""",
                (linha["id"],),
            )
            notificar(
                solicitante_upn,
                f"⚠️ Depois de 12h, a {tipo} do {identificador} ainda não aparece na lista de "
                f"movimentações do portal — precisa verificar.",
            )
            return

        status_raw = movimento.get("status", "")
        status_legivel = STATUS_PT.get(status_raw, status_raw)
        control_code = movimento.get("controlCode")

        horas = (datetime.datetime.now() - enviado_em_naive).total_seconds() / 3600
        if CICLO_NO_VIGIA and status_raw not in STATUS_FINAIS_CONHECIDOS and horas < REAGENDAR_ATE_HORAS:
            cur.execute("""UPDATE movimentacoes_hapvida_acompanhamento
                           SET ultima_checagem_em = now(), detalhe_status = %s,
                               proxima_checagem_em = now() + interval '2 hours'
                           WHERE id = %s""", (status_raw, linha["id"]))
            return
        if status_raw in STATUS_COM_CRITICA:
            if _rodar_ciclo(cur, linha, control_code):
                _registrar_recusas_exclusao(linha, codigo_contrato, control_code, None)
                cur.execute("""UPDATE movimentacoes_hapvida_acompanhamento
                               SET status = 'CONCLUIDO', ultima_checagem_em = now(), notificado_em = now(),
                                   detalhe_status = %s WHERE id = %s""", (status_raw, linha["id"]))
                return

        # #156 - relatorio de verdade (nome/CPF/motivo), nao so o status - ver #155
        try:
            resultado_vidas = ms.vidas_criticadas_de_movimentacao(project, codigo_contrato, control_code)
            vidas = resultado_vidas.get("vidasCriticadas") or []
        except Exception as e:
            vidas = []
            print(f"[{linha['id']}] erro buscando vidas criticadas: {e}")

        cur.execute(
            """UPDATE movimentacoes_hapvida_acompanhamento
               SET status = 'CONCLUIDO', ultima_checagem_em = now(), notificado_em = now(),
                   detalhe_status = %s
               WHERE id = %s""",
            (status_raw, linha["id"]),
        )

        extra_recusas = _registrar_recusas_exclusao(linha, codigo_contrato, control_code, vidas) + _datas_exclusao(linha)
        texto = (
            f"📋 Resultado final da {tipo} do {identificador}, 12h depois do envio: "
            f"**{status_legivel}**.\n\n"
        )
        if not vidas:
            texto += "Nenhuma vida com crítica ou cancelamento — tudo processou certinho. ✅" + extra_recusas + proxima_onda
            entregue = notificar(solicitante_upn, texto)
        else:
            # 13/10 (Raphael, C-MMIND): numa EXCLUSAO o relatorio lista as vidas canceladas - isso e' o sucesso, nao
            # critica. O aviso dizia "⚠️ 101 vida(s) com critica" e nao falava da onda 2; os dependentes ficaram 4 dias.
            if tipo == "exclusão":
                texto += (f"{len(vidas)} vida(s) no relatório da Hapvida (canceladas e eventuais críticas) — detalhe no anexo."
                          + extra_recusas + proxima_onda)
            else:
                texto += f"⚠️ {len(vidas)} vida(s) com crítica ou cancelamento — relatório completo no anexo." + extra_recusas
            entregue = notificar_com_relatorio(codigo_contrato, control_code, vidas, solicitante_upn, texto)
            if not entregue:
                # #156 - fallback: se o relatorio XLSX falhar por qualquer motivo, nao deixa o
                # solicitante sem nada - manda o texto corrido mesmo (pior formato, mas melhor
                # que silencio)
                entregue = notificar(solicitante_upn, texto + "\n\n(não consegui gerar o arquivo agora, avisando por texto)")
        if not entregue:
            _reagendar_por_entrega(cur, linha)


def run() -> None:
    conn = psycopg2.connect(**DB)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute(
        """SELECT * FROM movimentacoes_hapvida_acompanhamento
           WHERE status IN ('AGUARDANDO_CHECAGEM_INICIAL', 'AGUARDANDO_CHECAGEM_RELATORIO')
             AND proxima_checagem_em <= now()
           ORDER BY proxima_checagem_em ASC
           LIMIT 20"""
    )
    linhas = cur.fetchall()
    print(f"{datetime.datetime.now().isoformat()} - {len(linhas)} linha(s) pra processar")

    for linha in linhas:
        try:
            processar_linha(cur, linha)
        except Exception as e:
            print(f"[{linha['id']}] erro processando: {e}")

    lembrar_ondas_pendentes(cur, simular=os.environ.get("LEMBRETE_SIMULAR") == "1")

    cur.close()
    conn.close()


if __name__ == "__main__":
    run()
