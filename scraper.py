import json
import os
import time
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

import psycopg2
import secrets_store
from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PWTimeoutError
from playwright.sync_api import sync_playwright

SECRET_PREFIX = "hapvida-"

LOGIN_URL = "https://webhap.hapvida.com.br/pls/webhap/pk_controle_comissao.Login"
# Achado 25/08/2026: a Hapvida renomeou o pacote Oracle desta consulta de
# pk_controle_comissao pra pk_portal_nota_fiscal (login/pk_controle_comissao
# continua igual). A URL antiga passou a devolver 404 silencioso a partir de
# 21/08 - parse_table() nao achava a tabela, retornava [] sem lançar exceção,
# e upsert_pedidos() interpretava "sumiu da lista" como "foi pago", dando
# baixa falsa em 37 comissões reais (~R$1.013) que continuavam em aberto.
# Ver 150_bug_scraper_comissao_pk_portal_nota_fiscal_RESULTADO.md.
CONSULTA_URL_TEMPLATE = (
    "https://webhap.hapvida.com.br/pls/webhap/pk_portal_nota_fiscal.consultaNotaFiscal"
    "?pIdSessao={sessao}&pOpcao=I&pDtRef={ano}"
)

MAX_LOGIN_RETRIES = 2
LOGIN_INTERVAL_SECONDS = 8


class SessionExpiredError(Exception):
    pass


# --- Secret Manager -------------------------------------------------------
#
# Onde entraria um novo adapter de operadora: hoje só existe HapvidaAdapter,
# usando o prefixo "hapvida-" nos secrets. Uma nova operadora usaria seu
# próprio prefixo (ex: "operadoraX-") e sua própria classe de adapter com
# login()/fetch_open_invoices(), reaproveitando list_secrets_by_prefix,
# get_credentials, upsert_pedidos e build_summary.


CODIGOS_CONCESSIONARIA = ("128A", "05FK", "474A", "513A")  # RA + AVR (achado #84)

# #124 (12/08/2026): comissoes migrou do banco isolado "hapvida" pra dentro de "lifetools"
# (tabela `comissoes`, com operadora_id — comissão passa a ser rastreável por operadora,
# não só Hapvida). Este scraper só entende o portal Hapvida, então sempre grava com este id.
HAPVIDA_OPERADORA_ID = 1


def list_concessionaria_secrets(project: str) -> list[tuple[str, str]]:
    """Só os secrets de concessionária de comissão de verdade (RA/AVR).

    O prefixo "hapvida-" é reaproveitado por outros secrets que não têm
    nada a ver com o portal de comissão (censo por contrato legado,
    tipo "hapvida-S7HNK", e o portal-saúde, "hapvida-portal-saude") —
    sem esse filtro, run_all() tentava logar com TODOS eles no login
    de comissão (CNPJ+senha) e falhava 15x por dia, todo dia
    (pMsg=014, credencial errada) — ruído desnecessário no mesmo
    sistema anti-abuso que já bloqueou a automação do grupo legado com
    captcha. Achado e corrigido no #84.
    """
    result = []
    for secret_id in secrets_store.list_ids(SECRET_PREFIX, project):
        codigo = secret_id[len(SECRET_PREFIX):].upper()
        if codigo in CODIGOS_CONCESSIONARIA:
            result.append((codigo, secret_id))
    return result


def get_credentials(project: str, secret_id: str) -> dict:
    data = secrets_store.get_json(secret_id, project)
    return {"usuario": data["usuario"], "senha": data["senha"]}


# --- Parsing ---------------------------------------------------------------


def normalize_valor(raw: str) -> float:
    cleaned = raw.strip().replace("R$", "").strip()
    cleaned = cleaned.replace(".", "").replace(",", ".")
    return float(cleaned)


def parse_data(raw: str) -> date:
    return datetime.strptime(raw.strip(), "%d/%m/%Y").date()


def parse_table(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")

    target_table = None
    for table in soup.find_all("table"):
        text = table.get_text(" ", strip=True)
        if "Pedido" in text and "Valor" in text:
            target_table = table
            break
    if target_table is None:
        return []

    rows = target_table.find_all("tr")
    header_idx = None
    col_index: dict[str, int] = {}
    for i, row in enumerate(rows):
        cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
        if any("Pedido" in c for c in cells) and any("Valor" in c for c in cells):
            for idx, name in enumerate(cells):
                if "Pedido" in name:
                    col_index["numero_pedido"] = idx
                elif "Lote" in name:
                    col_index["lote"] = idx
                elif "Integra" in name:
                    col_index["data_integracao"] = idx
                elif "Valor" in name:
                    col_index["valor"] = idx
            header_idx = i
            break
    if header_idx is None:
        return []

    results = []
    for row in rows[header_idx + 1:]:
        cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
        if len(cells) <= max(col_index.values()):
            continue
        try:
            results.append(
                {
                    "numero_pedido": cells[col_index["numero_pedido"]],
                    "lote": cells[col_index["lote"]] if "lote" in col_index else "",
                    "data_integracao": parse_data(cells[col_index["data_integracao"]]),
                    "valor": normalize_valor(cells[col_index["valor"]]),
                }
            )
        except (ValueError, IndexError):
            continue
    return results


# --- Portal Hapvida ---------------------------------------------------------


class HapvidaAdapter:
    def __init__(self, page):
        self.page = page

    def login(self, usuario: str, senha: str) -> str:
        page = self.page
        page.goto(LOGIN_URL)
        page.fill("#pCnpj", usuario)
        page.fill("#pSenha", senha)
        page.click('input[value="PROSSEGUIR"]')
        page.wait_for_load_state("networkidle")

        try:
            page.click('button:has-text("Fechar")', timeout=3000)
        except PWTimeoutError:
            pass

        qs = parse_qs(urlparse(page.url).query)
        sessao = qs.get("pIdSessao", [None])[0]
        if not sessao:
            raise RuntimeError(
                f"Não foi possível extrair pIdSessao da URL pós-login: {page.url}"
            )
        return sessao

    def fetch_open_invoices(self, sessao: str, ano: int) -> list[dict]:
        url = CONSULTA_URL_TEMPLATE.format(sessao=sessao, ano=ano)
        page = self.page
        page.goto(url)
        content = page.content()
        if "SESSÃO EXPIROU" in content.upper() or "SESSAO EXPIROU" in content.upper():
            raise SessionExpiredError(f"Sessão expirou para pDtRef={ano}")
        return parse_table(content)


# --- Persistência ------------------------------------------------------------


def upsert_pedidos(conn, codigo: str, ano: int, hoje: date, rows: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT numero_pedido, lote FROM comissoes
            WHERE operadora_id = %s AND codigo_concessionaria = %s AND ano_ref = %s AND status = 'aberto'
            """,
            (HAPVIDA_OPERADORA_ID, codigo, ano),
        )
        previously_open = {(r[0], r[1] or "") for r in cur.fetchall()}

        seen = set()
        for row in rows:
            lote = row["lote"] or ""
            seen.add((row["numero_pedido"], lote))
            cur.execute(
                """
                INSERT INTO comissoes
                    (operadora_id, codigo_concessionaria, ano_ref, numero_pedido, lote, data_integracao,
                     valor, data_primeira_deteccao, data_ultima_verificacao, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'aberto')
                ON CONFLICT (operadora_id, codigo_concessionaria, numero_pedido, lote, ano_ref)
                DO UPDATE SET data_ultima_verificacao = EXCLUDED.data_ultima_verificacao,
                              status = 'aberto'
                """,
                (
                    HAPVIDA_OPERADORA_ID,
                    codigo,
                    ano,
                    row["numero_pedido"],
                    lote,
                    row["data_integracao"],
                    row["valor"],
                    hoje,
                    hoje,
                ),
            )

        for numero_pedido, lote in previously_open - seen:
            cur.execute(
                """
                UPDATE comissoes
                SET status = 'pago', data_baixa = %s
                WHERE operadora_id = %s AND codigo_concessionaria = %s AND ano_ref = %s
                  AND numero_pedido = %s AND lote = %s AND status = 'aberto'
                """,
                (hoje, HAPVIDA_OPERADORA_ID, codigo, ano, numero_pedido, lote),
            )
    conn.commit()


def build_summary(conn, hoje: date) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT codigo_concessionaria, numero_pedido, lote, valor, data_integracao
            FROM comissoes
            WHERE operadora_id = %s AND data_primeira_deteccao = %s AND data_integracao = %s
            ORDER BY codigo_concessionaria, numero_pedido
            """,
            (HAPVIDA_OPERADORA_ID, hoje, hoje),
        )
        novos = [
            {
                "codigo_concessionaria": r[0],
                "numero_pedido": r[1],
                "lote": r[2],
                "valor": float(r[3]),
                "data_integracao": r[4].isoformat(),
            }
            for r in cur.fetchall()
        ]

        cur.execute(
            """
            SELECT codigo_concessionaria, numero_pedido, lote, valor, data_integracao
            FROM comissoes
            WHERE operadora_id = %s AND status = 'aberto' AND data_integracao != %s
            ORDER BY data_integracao ASC, codigo_concessionaria
            """,
            (HAPVIDA_OPERADORA_ID, hoje),
        )
        atrasados = [
            {
                "codigo_concessionaria": r[0],
                "numero_pedido": r[1],
                "lote": r[2],
                "valor": float(r[3]),
                "data_integracao": r[4].isoformat(),
                "dias_em_aberto": (hoje - r[4]).days,
            }
            for r in cur.fetchall()
        ]

    return {
        "novos": novos,
        "atrasados": atrasados,
        "novos_count": len(novos),
        "atrasados_count": len(atrasados),
    }


def registrar_fonte_atualizacao() -> None:
    """#95: se auto-registra em fonte_atualizacoes, banco "lifetools" (mesma conexão que
    o resto do scraper usa desde #124 — mantida separada aqui só porque roda depois do
    conn principal já ter fechado). Best-effort: nunca derruba o job principal se a tabela
    não existir ou a permissão faltar."""
    try:
        conn_lt = psycopg2.connect(dbname="lifetools")
        with conn_lt.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fonte_atualizacoes
                    (estrutura, contrato_codigo, fonte_sistema, automatizado,
                     cadencia_esperada, ultima_atualizacao, atualizado_em)
                VALUES ('Comissões RA/AVR', NULL,
                        'n8n — workflow "Hapvida - Checagem de Comissoes" (hapvidaCheckV1)',
                        true, 'diário, 08:00 BRT (cron 0 8 * * *)', NOW(), NOW())
                ON CONFLICT (estrutura, COALESCE(contrato_codigo, ''))
                DO UPDATE SET ultima_atualizacao = NOW(), atualizado_em = NOW()
                """
            )
        conn_lt.commit()
        conn_lt.close()
    except Exception as e:
        print(f"[fonte_atualizacoes] aviso: não consegui atualizar o controle de freshness: {e}")


# --- Orquestração ------------------------------------------------------------


def run_all() -> dict:
    import comissao_detalhe  # importa scraper; aqui dentro para não virar import circular

    project = os.environ["GCP_PROJECT"]
    hoje = date.today()
    anos = [hoje.year, hoje.year - 1]

    erros: list[dict] = []
    detalhe: dict[str, dict] = {}
    # #124: comissao migrou pra dentro de "lifetools" — dbname explícito (não usa mais o
    # PGDATABASE=hapvida do .env, que era só pra esta conexão; nenhum outro script deste
    # container lê PGDATABASE implicitamente. A variável em si fica vestigial no .env até
    # o banco "hapvida" ser desativado — ver #124.10, não vale limpar o .env antes disso).
    conn = psycopg2.connect(dbname="lifetools")
    try:
        secrets = list_concessionaria_secrets(project)

        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                for i, (codigo, secret_id) in enumerate(secrets):
                    if i > 0:
                        # Espaça os logins para não esbarrar no limite anti-abuso
                        # do portal Hapvida quando várias concessionárias
                        # logam em sequência rápida (causa erro pMsg=008).
                        time.sleep(LOGIN_INTERVAL_SECONDS)
                    try:
                        creds = get_credentials(project, secret_id)
                    except Exception as e:
                        erros.append({"codigo": codigo, "erro": f"secret: {e}"})
                        continue

                    # contexto próprio: o download do detalhe abre uma segunda aba na mesma sessão
                    ctx = browser.new_context(accept_downloads=True)
                    page = ctx.new_page()
                    try:
                        adapter = HapvidaAdapter(page)
                        sessao = adapter.login(creds["usuario"], creds["senha"])

                        for ano in anos:
                            tentativas = 0
                            while True:
                                tentativas += 1
                                try:
                                    rows = adapter.fetch_open_invoices(sessao, ano)
                                    upsert_pedidos(conn, codigo, ano, hoje, rows)
                                    break
                                except SessionExpiredError:
                                    if tentativas >= MAX_LOGIN_RETRIES:
                                        erros.append(
                                            {
                                                "codigo": codigo,
                                                "ano": ano,
                                                "erro": "sessão expirou repetidamente",
                                            }
                                        )
                                        break
                                    sessao = adapter.login(
                                        creds["usuario"], creds["senha"]
                                    )

                        # 21/10/2026: detalhe por contrato de cada lote (comissoes_detalhe), na mesma
                        # sessão. Só tinha rodado à mão em 17/09 e ficou parado em 11/09. Best-effort:
                        # falha aqui vira erro do código, nunca derruba a checagem de pedidos.
                        try:
                            r = comissao_detalhe.coletar(
                                page, sessao, conn, codigo, comissao_detalhe.meses_recentes(hoje)
                            )
                            detalhe[codigo] = r
                            for msg in r["erros"]:
                                erros.append({"codigo": codigo, "erro": f"detalhe: {msg}"})
                        except Exception as e:
                            conn.rollback()
                            erros.append({"codigo": codigo, "erro": f"detalhe: {e}"})
                    except Exception as e:
                        erros.append({"codigo": codigo, "erro": str(e)})
                    finally:
                        ctx.close()
            finally:
                browser.close()

        summary = build_summary(conn, hoje)
    finally:
        conn.close()

    registrar_fonte_atualizacao()

    summary["erros"] = erros
    summary["detalhe"] = detalhe
    return summary
