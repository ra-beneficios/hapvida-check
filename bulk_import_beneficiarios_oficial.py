#!/usr/bin/env python3
"""Carga do formato OFICIAL de export do portal Hapvida
('Beneficiarios-contrato-<CODIGO>-...-Current.csv'): CSV ';'-separado, com
aspas, contendo CPF real de cada pessoa + vínculo titular/dependente
explícito + dados demográficos. Bem mais rico que bulk_import_censo.py
(que lê o CSV simplificado que eu monto manualmente via scraping).

Uso:
    python3 bulk_import_beneficiarios_oficial.py arquivo.csv [--commit]

Sem --commit: dry-run (ROLLBACK no final). Idempotente: reprocessar o mesmo
arquivo não duplica — upsert por CPF em beneficiarios, e em
movimentos_beneficiarios só cria linha nova se não existir; se já existir,
só faz BACKFILL de titular_beneficiario_id/data_adesao quando estavam NULL
(nunca sobrescreve um valor que já existia)."""
import csv
import os
import re
import sys
from datetime import date

import psycopg2

HAPVIDA_ID = 1


def normalizar_cpf(val):
    """CPF só-dígitos com 11 posições, repondo zeros à esquerda perdidos
    quando a origem gravou o CPF como número. Vazio/None -> None.
    Mais de 11 dígitos -> retorna só-dígitos como está (caso raro, monitorar)."""
    if val is None:
        return None
    s = re.sub(r"\D", "", str(val))
    if not s:
        return None
    if len(s) <= 11:
        return s.zfill(11)
    return s

DB = dict(
    host=os.getenv("DB_HOST", "localhost"),
    user=os.getenv("DB_USER", "radb"),
    password=os.environ["DB_PASSWORD"],
    dbname=os.getenv("DB_NAME", "lifetools"),
)


def parse_data(s: str):
    if not s or not s.strip():
        return None
    try:
        d, m, a = s.strip().split('/')
        return date(int(a), int(m), int(d))
    except Exception:
        return None


def main():
    if len(sys.argv) < 2:
        print("uso: bulk_import_beneficiarios_oficial.py arquivo.csv [--commit]")
        sys.exit(1)
    csv_path = sys.argv[1]
    commit = "--commit" in sys.argv

    try:
        with open(csv_path, encoding="utf-8-sig") as f:
            linhas = list(csv.DictReader(f, delimiter=";", quotechar='"'))
        if not linhas or not any(linhas[0].values()):
            raise UnicodeDecodeError("utf-8-sig", b"", 0, 1, "vazio, tenta latin-1")
    except UnicodeDecodeError:
        with open(csv_path, encoding="latin-1") as f:
            linhas = list(csv.DictReader(f, delimiter=";", quotechar='"'))

    # normaliza nomes de coluna com espaço pra underscore (formatos variam:
    # 'TIPO USUARIO' vs 'TIPO_USUARIO', 'GRAU PARENTESCO' vs 'GRAU_PARENTESCO',
    # 'NOME MAE' vs 'NOME_MAE', etc.) — deixa o resto do script sempre lendo
    # pela versão com underscore.
    linhas = [
        {k.strip().replace(" ", "_"): v for k, v in row.items() if k is not None}
        for row in linhas
    ]

    conn = psycopg2.connect(**DB)
    cur = conn.cursor()

    contrato_csv = linhas[0]["EMPRESA"].strip() if linhas else "?"
    cur.execute(
        """INSERT INTO lotes_importacao
               (operadora_id, template_id, tipo_lote, nome_arquivo, status, total_registros)
           VALUES (%s, NULL, 'INCLUSAO_OFICIAL', %s, 'PROCESSANDO', %s)
           RETURNING id""",
        (HAPVIDA_ID, os.path.basename(csv_path), len(linhas)),
    )
    lote_id = cur.fetchone()[0]

    canceladas = 0
    rejeitados = []
    resolved_id = [None] * len(linhas)  # beneficiario_id por linha, resolvido na 1a passada

    # 1a passada: upsert de TODOS os beneficiarios (titular pode aparecer
    # depois do dependente no arquivo — precisa existir antes de resolver o link)
    for i, row in enumerate(linhas):
        if row.get("DT_CANCELAMENTO", "").strip():
            canceladas += 1
            continue
        cpf = normalizar_cpf(row["CPF_DEPENDENTE"])
        nome = row["USUARIO"].strip()
        matricula = row["MATRICULA"].strip() or None
        nascimento = parse_data(row.get("DATA_NASCIMENTO", ""))
        sexo = (row.get("SEXO", "").strip() or None)
        nome_mae = (row.get("NOME_MAE", "").strip() or None)

        if cpf:
            cur.execute(
                """INSERT INTO beneficiarios (nome, cpf, matricula, data_nascimento, sexo, nome_mae)
                       VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (cpf) DO UPDATE
                       SET nome = EXCLUDED.nome, matricula = EXCLUDED.matricula,
                           data_nascimento = EXCLUDED.data_nascimento,
                           sexo = EXCLUDED.sexo, nome_mae = EXCLUDED.nome_mae,
                           updated_at = NOW()
                   RETURNING id""",
                (nome, cpf, matricula, nascimento, sexo, nome_mae),
            )
            resolved_id[i] = cur.fetchone()[0]
        else:
            # Sem CPF na origem (criança sem CPF processado ainda, por ex.).
            # Procura por alguém já gravado com mesmo nome+nascimento e cpf
            # NULL antes de criar, pra não duplicar em reprocessamento.
            cur.execute(
                "SELECT id FROM beneficiarios WHERE cpf IS NULL AND upper(nome) = upper(%s) "
                "AND data_nascimento IS NOT DISTINCT FROM %s",
                (nome, nascimento),
            )
            achado = cur.fetchone()
            if achado:
                resolved_id[i] = achado[0]
                cur.execute(
                    "UPDATE beneficiarios SET matricula = %s, sexo = %s, nome_mae = %s, updated_at = NOW() WHERE id = %s",
                    (matricula, sexo, nome_mae, achado[0]),
                )
            else:
                cur.execute(
                    """INSERT INTO beneficiarios (nome, cpf, matricula, data_nascimento, sexo, nome_mae)
                           VALUES (%s, NULL, %s, %s, %s, %s) RETURNING id""",
                    (nome, matricula, nascimento, sexo, nome_mae),
                )
                resolved_id[i] = cur.fetchone()[0]

    # 2a passada: resolve empresa/código, titular, e grava/atualiza o movimento
    gravados = 0
    atualizados = 0
    duplicados_sem_mudanca = 0
    for i, row in enumerate(linhas):
        if row.get("DT_CANCELAMENTO", "").strip():
            continue
        beneficiario_id = resolved_id[i]
        if beneficiario_id is None:
            continue
        cpf_titular = normalizar_cpf(row["CPF"])
        contrato = row["EMPRESA"].strip()
        plano = row.get("PLANO", "").strip() or None
        tipo = row.get("TIPO_USUARIO", "").strip().upper()
        data_adesao = parse_data(row.get("DT_ADMISSAO", ""))

        cur.execute(
            "SELECT id, empresa_id FROM empresa_operadora_codigos "
            "WHERE operadora_id = %s AND codigo_externo = %s",
            (HAPVIDA_ID, contrato),
        )
        codigo_row = cur.fetchone()
        if not codigo_row:
            identificador = row["CPF_DEPENDENTE"].strip() or row["USUARIO"].strip()
            rejeitados.append((identificador, f"contrato {contrato} não existe em empresa_operadora_codigos"))
            continue
        empresa_operadora_codigo_id, empresa_id = codigo_row

        titular_beneficiario_id = None
        relacao_dependencia = "01"
        if tipo == "DEPENDENTE":
            relacao_dependencia = "02"
            cur.execute("SELECT id FROM beneficiarios WHERE cpf = %s", (cpf_titular,))
            r = cur.fetchone()
            if r:
                titular_beneficiario_id = r[0]

        cur.execute(
            """SELECT id, titular_beneficiario_id, data_adesao FROM movimentos_beneficiarios
                   WHERE beneficiario_id = %s AND operadora_id = %s AND empresa_id = %s
                     AND empresa_operadora_codigo_id = %s AND tipo_movimento = 'INCLUSAO'""",
            (beneficiario_id, HAPVIDA_ID, empresa_id, empresa_operadora_codigo_id),
        )
        existente = cur.fetchone()

        if existente:
            mov_id, tit_atual, data_atual = existente
            sets, vals = [], []
            if tit_atual is None and titular_beneficiario_id is not None:
                sets.append("titular_beneficiario_id = %s"); vals.append(titular_beneficiario_id)
            if data_atual is None and data_adesao is not None:
                sets.append("data_adesao = %s"); vals.append(data_adesao)
            if sets:
                vals.append(mov_id)
                cur.execute(f"UPDATE movimentos_beneficiarios SET {', '.join(sets)} WHERE id = %s", vals)
                atualizados += 1
            else:
                duplicados_sem_mudanca += 1
            continue

        cur.execute(
            """INSERT INTO movimentos_beneficiarios
                   (lote_id, beneficiario_id, operadora_id, empresa_id, empresa_operadora_codigo_id,
                    tipo_movimento, plano, data_adesao, titular_beneficiario_id, relacao_dependencia, payload_json)
               VALUES (%s, %s, %s, %s, %s, 'INCLUSAO', %s, %s, %s, %s, %s)""",
            (lote_id, beneficiario_id, HAPVIDA_ID, empresa_id, empresa_operadora_codigo_id,
             plano, data_adesao, titular_beneficiario_id, relacao_dependencia,
             '{"origem": "bulk_import_beneficiarios_oficial"}'),
        )
        gravados += 1

    cur.execute(
        "UPDATE lotes_importacao SET status = 'CONCLUIDO', processed_at = NOW() WHERE id = %s",
        (lote_id,),
    )

    print(f"Contrato: {contrato_csv}")
    print(f"Linhas no CSV: {len(linhas)}")
    print(f"Puladas (DT_CANCELAMENTO preenchido): {canceladas}")
    print(f"Gravados (movimento novo): {gravados}")
    print(f"Atualizados (backfill de titular/data em movimento existente): {atualizados}")
    print(f"Sem mudança (já estava completo): {duplicados_sem_mudanca}")
    print(f"Rejeitados: {len(rejeitados)}")
    for cpf, motivo in rejeitados[:20]:
        print(f"  - {cpf}: {motivo}")

    if commit:
        conn.commit()
        print("COMMIT feito.")
    else:
        conn.rollback()
        print("DRY-RUN — nada foi gravado.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
