"""
Captura de faturas/boletos do Grupo Saúde (0KFXW/WARQU + coligadas), via
`billings/partner-company/{codigo}/historic-bills/search` na mesma API
moderna do portal-empresa que censo_saude.py e copart_saude.py já usam
(achado no #83, testado em escala no #84 — schema da resposta bate
exatamente com as colunas que `bulk_import_faturas.py` já espera).

**Achado real 13/09/2026:** a Hapvida quebrou o endpoint antigo
`bills/search` (404 em 100% dos códigos, confirmado não ser sessão — a NF
funcionava normal com a mesma sessão) e o substituiu por DOIS endpoints
separados, descobertos navegando a UI de verdade com interceptação de
rede (login -> Menu > Financeiro > Ver todas as faturas):
- `unpaid-bills/search` — faturas em aberto (inclui a do mês corrente).
- `historic-bills/search` — só as JÁ LIQUIDADAS, mesmíssimo schema do
  endpoint antigo (billingNumber, number, period, dueDate, amount,
  billType, status, statusCode, healthOperator, remittence, personNumber).

**Correção 28/09/2026:** até aqui só líamos `historic-bills`, achando que
era o histórico completo — não é, fatura em aberto nunca aparece nele.
Resultado: o boleto de OUT26 (já emitido no portal, venc. 10/10) não
entrava em `faturas` e o envio de documentos baixava SET26 de novo. O
`bills/search` antigo = união dos dois, então agora consultamos AMBOS.

Reaproveita login/sessão de censo_saude.py e o escopo de códigos
(Grupo Saúde, sem Odonto Premium) de copart_saude.py.

Achado ao testar (01/08): o endpoint retorna ocasionalmente 400 com
"ORA-01013 - o usuário solicitou o cancelamento da operação atual" — erro
transitório do Oracle no backend da Hapvida, não uma rejeição de parâmetro
(confirmado repetindo a MESMA chamada e vendo ora 200 ora 400). Por isso
cada página tem retry curto embutido.
"""
import time
from datetime import date, datetime

import httpx
import psycopg2
from playwright.sync_api import sync_playwright

from censo_saude import API_BASE, _headers_http, criar_sessao_empresa, login_saude
from copart_saude import listar_codigos_grupo_saude

PAGE_LIMIT = 50
MAX_TENTATIVAS_PAGINA = 3


def _parse_data_br(data_str):
    if not data_str:
        return None
    try:
        return datetime.strptime(data_str, "%d/%m/%Y").date()
    except ValueError:
        return None


_TIMEOUT_PAGINA = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


def _normalizar_status(status_bruto: str | None) -> str | None:
    """Normaliza o texto de status pro padrao Em Aberto / Pago (Andre,
    08/09/2026) - o portal devolve varios textos ('Liquidado', 'Enviado
    Cobranca', 'Em Aberto/Cobranca') que na pratica significam so' duas
    coisas: pago ou nao. Gravar ja normalizado na fonte evita que o cron
    diario sobrescreva a normalizacao feita direto no banco (#152)."""
    if not status_bruto:
        return status_bruto
    return "Pago" if "liquidado" in status_bruto.lower() or "pago" in status_bruto.lower() else "Em Aberto"


# Ordem importa pouco (uma fatura nunca está nos dois ao mesmo tempo), mas
# unpaid por último faz o status "Em Aberto" prevalecer se o portal
# devolver a mesma fatura nos dois durante a transição de baixa.
ENDPOINTS_FATURAS = ("historic-bills", "unpaid-bills")


def capturar_boletos_contrato(auth: dict, codigo: str, data_inicio: str, data_fim: str) -> list[dict]:
    """Pagina historic-bills/search e unpaid-bills/search inteiros pra 1 código (ver
    correção 28/09 no topo). data_inicio/data_fim em YYYY-MM-DD.

    Achado 01/08: uma chamada com timeout=60 (uniforme) ficou pendurada por >30min sem
    estourar timeout nem erro — suspeita de handshake TLS travado silenciosamente. Timeout
    explícito por fase (connect/read/write/pool), todos curtos, pra nunca mais confiar numa
    única chamada indefinidamente."""
    criar_sessao_empresa(auth, codigo)

    por_chave = {}
    for endpoint in ENDPOINTS_FATURAS:
        for linha in _paginar_faturas(auth, codigo, endpoint, data_inicio, data_fim):
            por_chave[(linha["billing_number"], linha["numero"])] = linha
    return list(por_chave.values())


def _paginar_faturas(auth: dict, codigo: str, endpoint: str, data_inicio: str, data_fim: str) -> list[dict]:
    linhas = []
    offset = 0
    while True:
        url = f"{API_BASE}/billings/partner-company/{codigo}/{endpoint}/search"
        params = {"start-date": data_inicio, "end-date": data_fim, "offset": offset, "limit": PAGE_LIMIT}

        content = None
        ultimo_erro = None
        for tentativa in range(MAX_TENTATIVAS_PAGINA):
            try:
                r = httpx.get(url, headers=_headers_http(auth), params=params, timeout=_TIMEOUT_PAGINA)
            except httpx.TimeoutException as e:
                ultimo_erro = f"timeout: {e}"
                time.sleep(2)
                continue
            if r.status_code == 200:
                content = r.json().get("content", {})
                break
            ultimo_erro = r.text[:300]
            time.sleep(2)
        if content is None:
            raise RuntimeError(f"{endpoint}/search falhou {MAX_TENTATIVAS_PAGINA}x pro código {codigo}: {ultimo_erro}")

        bills = content.get("bills", [])
        for b in bills:
            linhas.append(
                {
                    "partner_company": b.get("partnerCompany") or codigo,
                    "billing_number": b.get("billingNumber"),
                    "numero": b.get("number"),
                    "competencia": _parse_data_br(b.get("period")),
                    "vencimento": _parse_data_br(b.get("dueDate")),
                    "valor": b.get("amount"),
                    "bill_type": b.get("billType"),
                    "status": _normalizar_status(b.get("status")),
                    "status_code": b.get("statusCode"),
                    "health_operator": b.get("healthOperator"),
                    "remittence": b.get("remittence"),
                    "person_number": b.get("personNumber"),
                }
            )

        total = content.get("total", 0)
        offset += PAGE_LIMIT
        if offset >= total or not bills:
            break

    return linhas


def upsert_faturas(linhas: list[dict], fonte: str) -> dict:
    """Upsert por (billing_number, numero) — mesma chave/lógica do bulk_import_faturas.py (#45),
    resolvendo o contrato pelo codigo_externo = partner_company."""
    if not linhas:
        return {"novos": 0, "atualizados": 0, "rejeitados": 0, "rejeitados_detalhe": []}

    novos = atualizados = rejeitados = 0
    rejeitados_detalhe = []
    conn = psycopg2.connect(dbname="lifetools")
    try:
        with conn.cursor() as cur:
            for linha in linhas:
                cur.execute(
                    "SELECT id FROM empresa_operadora_codigos WHERE operadora_id = 1 AND codigo_externo = %s",
                    (linha["partner_company"],),
                )
                row = cur.fetchone()
                if not row:
                    rejeitados += 1
                    rejeitados_detalhe.append({"codigo": linha["partner_company"], "billing_number": linha["billing_number"], "motivo": "código não cadastrado"})
                    continue

                cur.execute(
                    """
                    INSERT INTO faturas
                        (empresa_operadora_codigo_id, partner_company, billing_number, numero,
                         competencia, vencimento, valor, bill_type, status, status_code,
                         health_operator, remittence, person_number, coletado_em, fonte)
                    VALUES
                        (%(eoc_id)s, %(partner_company)s, %(billing_number)s, %(numero)s,
                         %(competencia)s, %(vencimento)s, %(valor)s, %(bill_type)s, %(status)s, %(status_code)s,
                         %(health_operator)s, %(remittence)s, %(person_number)s, NOW(), %(fonte)s)
                    ON CONFLICT (billing_number, numero) DO UPDATE SET
                        status = EXCLUDED.status, status_code = EXCLUDED.status_code,
                        valor = EXCLUDED.valor, vencimento = EXCLUDED.vencimento,
                        coletado_em = NOW(), fonte = EXCLUDED.fonte
                    RETURNING (xmax = 0) AS inserido
                    """,
                    {**linha, "eoc_id": row[0], "fonte": fonte},
                )
                if cur.fetchone()[0]:
                    novos += 1
                else:
                    atualizados += 1
        conn.commit()
    finally:
        conn.close()

    return {"novos": novos, "atualizados": atualizados, "rejeitados": rejeitados, "rejeitados_detalhe": rejeitados_detalhe}


def registrar_fonte_atualizacao_boletos(total_linhas: int) -> None:
    conn = psycopg2.connect(dbname="lifetools")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fonte_atualizacoes
                    (estrutura, contrato_codigo, fonte_sistema, automatizado, cadencia_esperada,
                     ultima_atualizacao, registros_afetados, observacao, atualizado_em)
                VALUES ('Boletos/Faturas Grupo Saúde', NULL,
                        'Sistema 1 (portal-empresa, API) — billings/partner-company/{codigo}/historic-bills/search + unpaid-bills/search', true,
                        'diário',
                        NOW(), %s,
                        'Só Grupo Saúde (0KFXW/WARQU + coligadas, sem Odonto Premium). Grupo Legado continua sem captura automática (portal diferente, bloqueado por captcha).',
                        NOW())
                ON CONFLICT (estrutura, COALESCE(contrato_codigo, '')) DO UPDATE SET
                    ultima_atualizacao = NOW(), registros_afetados = %s, atualizado_em = NOW()
                """,
                (total_linhas, total_linhas),
            )
        conn.commit()
    finally:
        conn.close()


def run_boletos_saude(usuario: str, senha: str, data_inicio: str = None, data_fim: str = None) -> dict:
    """Sem data_inicio/data_fim, usa uma janela ampla o bastante pra cobrir o que a tabela
    `faturas` já tem hoje (vencimento min 2025-06-10) até 4 meses à frente."""
    if data_inicio is None:
        data_inicio = "2025-01-01"
    if data_fim is None:
        data_fim = date.today().replace(year=date.today().year + 1).isoformat()

    codigos = listar_codigos_grupo_saude()
    fonte = f"billings_saude_api_{date.today().isoformat()}"

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        try:
            auth = login_saude(page, usuario, senha)
        finally:
            browser.close()

    resultados = []
    total_novos = total_atualizados = total_rejeitados = 0
    for codigo in codigos:
        print(f"[boletos_saude] processando {codigo}...", flush=True)
        try:
            try:
                linhas = capturar_boletos_contrato(auth, codigo, data_inicio, data_fim)
            except Exception as e:
                # 01/10/2026: a sessao da Hapvida expira no meio da rodada e todo
                # codigo seguinte dava 401 - reloga uma vez e tenta de novo.
                if "401" not in str(e):
                    raise
                print(f"[boletos_saude] {codigo}: 401, relogando e tentando de novo", flush=True)
                with sync_playwright() as pw:
                    browser = pw.chromium.launch()
                    page = browser.new_page()
                    try:
                        auth = login_saude(page, usuario, senha)
                    finally:
                        browser.close()
                linhas = capturar_boletos_contrato(auth, codigo, data_inicio, data_fim)
            resumo = upsert_faturas(linhas, fonte)
            total_novos += resumo["novos"]
            total_atualizados += resumo["atualizados"]
            total_rejeitados += resumo["rejeitados"]
            resultados.append({"codigo": codigo, "status": "ok", "faturas": len(linhas), **resumo})
            print(f"[boletos_saude] {codigo} ok — {len(linhas)} faturas", flush=True)
        except Exception as e:
            resultados.append({"codigo": codigo, "status": "erro", "detalhe": str(e)})
            print(f"[boletos_saude] {codigo} ERRO — {e}", flush=True)

    try:
        registrar_fonte_atualizacao_boletos(total_novos + total_atualizados)
    except Exception as e:
        print(f"[fonte_atualizacoes] aviso: não consegui atualizar o controle de freshness: {e}")

    return {
        "total_codigos": len(codigos),
        "total_novos": total_novos,
        "total_atualizados": total_atualizados,
        "total_rejeitados": total_rejeitados,
        "resultados": resultados,
    }
