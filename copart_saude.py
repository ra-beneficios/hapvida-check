"""
Captura o detalhe por procedimento do Copart (Grupo Saúde: 0KFXW/WARQU +
coligadas), via o mesmo endpoint "prévia de coparticipação" do portal-empresa
(#83/#106). Reaproveita login/sessão de censo_saude.py.

Achado real confirmado antes de implementar (#106): o detalhe some do
endpoint depois que a Hapvida fecha/fatura o ciclo — testado em produção,
comparando com faturas já emitidas em `faturas` (banco lifetools):
- IXP2R (tinha fatura de Copart fechada em 18/07/2026) -> preview-report
  voltou completamente vazio (0 beneficiários) alguns dias depois.
- EM593/KGBQY (mesma fatura de 18/07/2026) -> preview-report só mostra
  procedimentos com data de autorização POSTERIOR a 18/07 — o ciclo já
  faturado não aparece mais, só o próximo ciclo (ainda aberto) acumulando
  do zero. Por isso a captura precisa ser diária (decisão tomada com essa
  evidência, não só precaução).

Cada item de procedimento tem um "id" (UUID) próprio da API da Hapvida,
usado como chave de upsert (mais confiável que compor uma chave sintética
de ciclo, que o endpoint não expõe explicitamente).
"""
import os
import re
from datetime import date, datetime

import httpx
import psycopg2
from playwright.sync_api import sync_playwright

from censo_saude import API_BASE, _headers_http, criar_sessao_empresa, login_saude

PAGE_LIMIT = 50


def normalizar_cpf(val):
    """Mesmo padrão já usado em service/api/main.py e bulk_import_beneficiarios_oficial.py
    (#70) — só dígitos, 11 posições, zero à esquerda reposto. Vazio/None -> None."""
    if val is None:
        return None
    s = re.sub(r"\D", "", str(val))
    if not s:
        return None
    if len(s) <= 11:
        return s.zfill(11)
    return s


def _parse_data_br(data_str):
    if not data_str:
        return None
    try:
        return datetime.strptime(data_str, "%d/%m/%Y").date()
    except ValueError:
        return None


def listar_codigos_grupo_saude() -> list[str]:
    """Todos os codigo_externo SAÚDE/ODONTO_PURO sob a árvore 0KFXW/WARQU (mães + coligadas) —
    mesma definição de "Grupo Saúde" usada no #83/#106 (não inclui 9WLZU/Boaventura nem outras
    mães, escopo explícito do #106).

    #117: migrado pra ler de vw_contratos (camada de verdade única, mesma view que
    dashboard_export.py e a Cora já usam) em vez de empresa_operadora_codigos direto — era
    mais um lugar reimplementando a mesma definição de "grupo" na mão.

    Exclui ODONTO_PREMIUM de propósito (achado confirmado 01/08, com o André): TODO código
    Premium — os 11 do 0KFXW, mais SY8MI/DAVJV e 9GBKA/9WLZU — vive no portal Grupo Legado
    (login diferente, sem relação com a API moderna que WARQU/0KFXW usam), nunca na API
    moderna. Antes disso, os 11 Premium do 0KFXW entravam nesta lista e falhavam com 400
    ("usuário não possui acesso ao contrato X") — não era bug de código, era a API confirmando
    que esse acesso não existe por este caminho. Regra geral, sem exceção conhecida.

    26/10/2026 (André): Odonto Puro nunca tem fatura (SQL 184 trava gera_boleto = false) e contrato do
    Legado (Z8TUR) não abre sessão no portal moderno - os dois saem. Não filtrar por gera_boleto:
    F5T8L/59CD2/YXF01 são Saúde com gera_boleto = false e têm fatura."""
    conn = psycopg2.connect(dbname="lifetools")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT codigo_externo FROM vw_contratos
                WHERE (codigo_contrato_mae IN ('0KFXW', 'WARQU') OR codigo_externo IN ('0KFXW', 'WARQU'))
                  AND modalidade NOT IN ('ODONTO_PREMIUM', 'ODONTO_PURO')
                  AND portal = 'MODERNO'
                ORDER BY codigo_externo
                """
            )
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def capturar_copart_contrato(auth: dict, codigo: str) -> list[dict]:
    """Pagina coparticipation-preview-report inteiro pra 1 código, devolve uma linha por
    procedimento (achatado — a API agrupa por beneficiário, nós desmontamos)."""
    criar_sessao_empresa(auth, codigo)

    linhas = []
    offset = 0
    while True:
        url = (
            f"{API_BASE}/billings/partner-company/{codigo}/coparticipation-preview-report"
            f"?offset={offset}&limit={PAGE_LIMIT}"
        )
        r = httpx.get(url, headers=_headers_http(auth), timeout=30)
        r.raise_for_status()
        content = r.json().get("content", {})
        reports = content.get("coparticipationPreviewReports", [])

        for rep in reports:
            total_beneficiario = rep.get("totalCoparticipation")
            for proc in rep.get("beneficiaryProcedures", []):
                linhas.append(
                    {
                        "hapvida_procedure_id": proc.get("id"),
                        "contrato_codigo": codigo,
                        "beneficiario_nome": rep.get("beneficiaryName"),
                        "beneficiario_documento": normalizar_cpf(rep.get("beneficiaryDocument")),
                        "codigo_procedimento": proc.get("procedureCode"),
                        "descricao_procedimento": proc.get("procedureDescription"),
                        "numero_autorizacao": proc.get("authorization"),
                        "codigo_prestador": proc.get("providerCode"),
                        "nome_prestador": proc.get("corporateReason"),
                        "data_autorizacao": _parse_data_br(proc.get("authorizationDate")),
                        "valor_coparticipacao": proc.get("coparticipationValue"),
                        "total_coparticipacao_beneficiario": total_beneficiario,
                    }
                )

        total = content.get("total", 0)
        offset += PAGE_LIMIT
        if offset >= total or not reports:
            break

    return linhas


def upsert_copart(linhas: list[dict]) -> int:
    """Grava em copart_saude_detalhe — upsert por (contrato+CPF+autorização+procedimento).

    Achado real ao testar (#106): o "id" que a API devolve por procedimento NÃO é estável
    entre chamadas (rodando a captura 2x, veio um id novo pra cada procedimento, mesmo sendo o
    mesmo item) — usar ele como chave de upsert duplicava 100% a cada rodada. A chave de negócio
    (contrato_codigo, beneficiario_documento, numero_autorizacao, codigo_procedimento) é o que
    de fato identifica o mesmo procedimento entre capturas."""
    if not linhas:
        return 0

    ciclo_referencia = date.today().strftime("%Y-%m")
    conn = psycopg2.connect(dbname="lifetools")
    try:
        with conn.cursor() as cur:
            for linha in linhas:
                cur.execute(
                    """
                    INSERT INTO copart_saude_detalhe
                        (hapvida_procedure_id, contrato_codigo, beneficiario_nome, beneficiario_documento,
                         codigo_procedimento, descricao_procedimento, numero_autorizacao, codigo_prestador,
                         nome_prestador, data_autorizacao, valor_coparticipacao,
                         total_coparticipacao_beneficiario, ciclo_referencia, atualizado_em)
                    VALUES (%(hapvida_procedure_id)s, %(contrato_codigo)s, %(beneficiario_nome)s,
                            %(beneficiario_documento)s, %(codigo_procedimento)s, %(descricao_procedimento)s,
                            %(numero_autorizacao)s, %(codigo_prestador)s, %(nome_prestador)s,
                            %(data_autorizacao)s, %(valor_coparticipacao)s,
                            %(total_coparticipacao_beneficiario)s, %(ciclo_referencia)s, NOW())
                    ON CONFLICT (contrato_codigo, beneficiario_documento, numero_autorizacao, codigo_procedimento)
                    DO UPDATE SET
                        hapvida_procedure_id = EXCLUDED.hapvida_procedure_id,
                        beneficiario_nome = EXCLUDED.beneficiario_nome,
                        valor_coparticipacao = EXCLUDED.valor_coparticipacao,
                        total_coparticipacao_beneficiario = EXCLUDED.total_coparticipacao_beneficiario,
                        ciclo_referencia = EXCLUDED.ciclo_referencia,
                        atualizado_em = NOW()
                    """,
                    {**linha, "ciclo_referencia": ciclo_referencia},
                )
        conn.commit()
    finally:
        conn.close()

    return len(linhas)


def registrar_fonte_atualizacao_copart(total_linhas: int) -> None:
    """#106 item 6: nova linha em fonte_atualizacoes, específica do Grupo Saúde — a linha
    genérica 'Copart / Extrato de Utilização' criada no #95 continua representando o que ainda
    não foi implementado (Grupo Legado), sem inventar data pra ela."""
    conn = psycopg2.connect(dbname="lifetools")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fonte_atualizacoes
                    (estrutura, contrato_codigo, fonte_sistema, automatizado, cadencia_esperada,
                     ultima_atualizacao, registros_afetados, observacao, atualizado_em)
                VALUES ('Copart Grupo Saúde — detalhe por procedimento', NULL,
                        'Sistema 1 (portal-empresa, API) — coparticipation-preview-report', true,
                        'diário (captura precisa rodar antes de cada ciclo fechar — #106)',
                        NOW(), %s,
                        'Detalhe por procedimento do ciclo aberto — desaparece do endpoint após faturar (confirmado com teste real no #106). Só Grupo Saúde (0KFXW/WARQU); Grupo Legado continua sem captura, ver linha "Copart / Extrato de Utilização".',
                        NOW())
                ON CONFLICT (estrutura, COALESCE(contrato_codigo, '')) DO UPDATE SET
                    ultima_atualizacao = NOW(), registros_afetados = %s, atualizado_em = NOW()
                """,
                (total_linhas, total_linhas),
            )
        conn.commit()
    finally:
        conn.close()


def run_copart_saude(usuario: str, senha: str) -> dict:
    codigos = listar_codigos_grupo_saude()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        try:
            auth = login_saude(page, usuario, senha)
        finally:
            browser.close()

    resultados = []
    total_geral = 0
    for codigo in codigos:
        try:
            linhas = capturar_copart_contrato(auth, codigo)
            gravadas = upsert_copart(linhas)
            total_geral += gravadas
            resultados.append({"codigo": codigo, "status": "ok", "procedimentos": gravadas})
        except Exception as e:
            resultados.append({"codigo": codigo, "status": "erro", "detalhe": str(e)})

    try:
        registrar_fonte_atualizacao_copart(total_geral)
    except Exception as e:
        print(f"[fonte_atualizacoes] aviso: não consegui atualizar o controle de freshness: {e}")

    return {"total_codigos": len(codigos), "total_procedimentos": total_geral, "resultados": resultados}
