"""
Regra de auto-commit pro /run-censo, baseada no Plano_Automacao_Censo_Hapvida.md.

IMPORTANTE: implementado e disponivel, mas NAO chamado por nenhum
endpoint nesta rodada (#59). O André decide quando ativar de verdade,
depois de ver alguns ciclos de dry-run rodando limpo.

Limitacao conhecida: os loaders (bulk_import_beneficiarios_oficial.py)
rodam em subprocess com sua propria transacao (BEGIN...ROLLBACK), que ja
fecha antes dessas checagens rodarem. Pra uso real em producao, o ideal
e rodar essas checagens DENTRO da mesma transacao do commit (antes do
COMMIT final), nao depois - como esta aqui, elas comparam o estado atual
do banco (pos-dry-run-com-rollback == estado antes de qualquer carga),
o que já serve pra decidir SE vale a pena commitar, mas nao substitui
reter a mesma conexao/transacao no momento do commit real.
"""
import os

import psycopg2

DB = dict(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "radb"),
    password=os.environ.get("DB_PASSWORD"),
    dbname=os.getenv("DB_NAME", "lifetools"),
)


def _conectar():
    return psycopg2.connect(**DB)


def contar_cpf_malformado() -> int:
    """CPFs que nao tem 11 digitos e nao sao NULL - deveria ser 0 (fonte
    limpa desde o #26)."""
    conn = _conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM beneficiarios WHERE cpf IS NOT NULL AND length(cpf) <> 11"
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def tem_cpf_malformado_novo(baseline: int | None = None) -> bool:
    """Se baseline for informado (contagem ANTES da carga), retorna True
    só se a contagem atual for MAIOR (regressão de verdade). Sem
    baseline, usa uma checagem mais simples: qualquer CPF malformado
    já é motivo de alerta, dado que a fonte está limpa desde o #26."""
    atual = contar_cpf_malformado()
    if baseline is not None:
        return atual > baseline
    return atual > 0


def tem_orfao_fk() -> bool:
    """Mesmas 3 checagens de órfão usadas em todo o projeto (#48, #51):
    movimento apontando pra beneficiario inexistente, titular_beneficiario_id
    apontando pra beneficiario inexistente, fatura apontando pra código
    inexistente. Deveria ser sempre False."""
    conn = _conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM movimentos_beneficiarios mb "
                "LEFT JOIN beneficiarios b ON b.id = mb.beneficiario_id WHERE b.id IS NULL"
            )
            orfaos_mov = cur.fetchone()[0]

            cur.execute(
                "SELECT count(*) FROM movimentos_beneficiarios mb "
                "WHERE mb.titular_beneficiario_id IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM beneficiarios b WHERE b.id = mb.titular_beneficiario_id)"
            )
            orfaos_titular = cur.fetchone()[0]

            cur.execute(
                "SELECT count(*) FROM faturas f "
                "LEFT JOIN empresa_operadora_codigos e ON e.id = f.empresa_operadora_codigo_id "
                "WHERE e.id IS NULL"
            )
            orfaos_fatura = cur.fetchone()[0]
        return (orfaos_mov + orfaos_titular + orfaos_fatura) > 0
    finally:
        conn.close()


def modalidade_bate_com_cadastro(codigo_externo: str, modalidade_esperada: str) -> bool:
    """Confirma que a modalidade cadastrada pro código bate com o
    esperado - evita repetir o tipo de corrupção histórica do HC2
    (#10), onde a modalidade mudou sem ninguém perceber."""
    conn = _conectar()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT modalidade FROM empresa_operadora_codigos WHERE codigo_externo = %s",
                (codigo_externo,),
            )
            row = cur.fetchone()
        if row is None:
            return False
        return row[0] == modalidade_esperada
    finally:
        conn.close()


def pode_auto_commitar(resultado_dry_run: dict, codigo_externo: str = None,
                        modalidade_esperada: str = None,
                        baseline_cpf_malformado: int = None) -> bool:
    """Regra objetiva do Plano_Automacao_Censo_Hapvida.md. Todas as
    condições precisam ser verdadeiras. NÃO chamado em produção ainda
    (#59) - só implementado e disponível pro André ativar depois."""
    if resultado_dry_run.get("rejeitados", 1) != 0:
        return False
    if tem_cpf_malformado_novo(baseline_cpf_malformado):
        return False
    if tem_orfao_fk():
        return False
    if codigo_externo and modalidade_esperada:
        if not modalidade_bate_com_cadastro(codigo_externo, modalidade_esperada):
            return False
    return True
