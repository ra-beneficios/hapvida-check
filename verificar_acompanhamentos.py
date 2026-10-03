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


def notificar(upn: str, texto: str) -> bool:
    if not upn:
        return False
    try:
        resp = requests.post(CORA_NOTIFICAR_URL, json={"upn": upn, "texto": texto}, timeout=15)
        return resp.ok
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
        return resp_cora.ok
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
            notificar(
                solicitante_upn,
                f"✅ A {tipo} do {identificador} já processou no portal Hapvida — status: "
                f"**{status_legivel}**. Se precisar do relatório detalhado de críticas, me avisa "
                f"que eu busco." + proxima_onda,
            )
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

        texto = (
            f"📋 Resultado final da {tipo} do {identificador}, 12h depois do envio: "
            f"**{status_legivel}**.\n\n"
        )
        if not vidas:
            texto += "Nenhuma vida com crítica ou cancelamento — tudo processou certinho. ✅" + proxima_onda
            notificar(solicitante_upn, texto)
        else:
            texto += f"⚠️ {len(vidas)} vida(s) com crítica ou cancelamento — relatório completo no anexo."
            enviado = notificar_com_relatorio(codigo_contrato, control_code, vidas, solicitante_upn, texto)
            if not enviado:
                # #156 - fallback: se o relatorio XLSX falhar por qualquer motivo, nao deixa o
                # solicitante sem nada - manda o texto corrido mesmo (pior formato, mas melhor
                # que silencio)
                notificar(solicitante_upn, texto + "\n\n(não consegui gerar o arquivo agora, avisando por texto)")


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

    cur.close()
    conn.close()


if __name__ == "__main__":
    run()
