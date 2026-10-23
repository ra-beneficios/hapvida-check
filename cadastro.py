"""Cadastro único de empresas Hapvida (SQL 183, 23/10/2026): o que antes estava fixo no código ou na planilha do Cowork.

A tela Empresas da intranet edita empresa_operadora_codigos; aqui só se lê. Nenhuma lista de contrato fica no código:
contrato novo cadastrado pelo pós-vendas entra sozinho no faturamento, no Legado e na contagem por grupo.
"""
import psycopg2

HAPVIDA = 1


def _linhas(sql: str, params: tuple = ()) -> list[tuple]:
    conn = psycopg2.connect(dbname="lifetools")
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def contratos_legado() -> dict[str, str]:
    """codigo -> empresa: contratos ativos do portal Legado que entram no faturamento (substitui a aba 'Contratos (55)'
    de empresas_hapvida.xlsx). Odonto Puro fica fora, como na planilha."""
    return dict(_linhas(
        """SELECT eoc.codigo_externo, e.nome FROM empresa_operadora_codigos eoc JOIN empresas e ON e.id = eoc.empresa_id
            WHERE eoc.operadora_id = %s AND eoc.portal = 'LEGADO' AND eoc.situacao = 'ATIVO'
              AND eoc.envia_faturamento AND eoc.modalidade <> 'ODONTO_PURO' ORDER BY 1""", (HAPVIDA,)))


def sem_faturamento() -> set[str]:
    """Ativos fora do download/envio mensal (envia_faturamento = false). Era faturamento_mensal.SEM_FATURAMENTO."""
    return {c for (c,) in _linhas(
        "SELECT codigo_externo FROM empresa_operadora_codigos WHERE operadora_id = %s AND NOT envia_faturamento",
        (HAPVIDA,))}


def maes_portal_moderno() -> list[str]:
    """Mães (código com grupo_nome) do portal moderno: o relatório Full delas traz as coligadas. Era
    censo_saude.MAES_SAUDE (+ DAVJV, que vinha à parte).
    Menor árvore primeiro (o Full da 0KFXW passa de 40 min)."""
    return [c for (c,) in _linhas(
        """SELECT m.codigo_externo FROM empresa_operadora_codigos m
            WHERE m.operadora_id = %s AND m.grupo_nome IS NOT NULL AND m.portal = 'MODERNO' AND m.situacao = 'ATIVO'
            ORDER BY (SELECT count(*) FROM empresa_operadora_codigos f
                       WHERE f.operadora_id = m.operadora_id AND f.codigo_contrato_mae = m.codigo_externo), 1""",
        (HAPVIDA,))]
