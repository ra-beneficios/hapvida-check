# CHANGELOG — hapvida-check

Uma entrada por publicação (`infra/deploy/publicar_hapvida_check.sh` no lifetools). Reverter: `--reverter <tag anterior>`.

## hc-2026.10.21-3 (21/10/2026) — hapvida-check c60b9df + lifetools d141ff8 · reverter: hc-2026.10.21-2
- TXT do Odonto Premium: linha aceita no Saúde (não a última tentativa), uma por CPF, sem data impossível (ORA-01843 de 19/10, MIGUEL 4DFEV).

## hc-2026.10.21-2 (21/10/2026) — hapvida-check ced6842 + lifetools e6debdd · reverter: hc-2026.10.21-1
- fechar_contrato: coleta incompleta nunca vira relatório — completa só os CPFs que falharam em até 3 sessões novas; se ainda faltar, o entregar recusa (cron 05:15 mantém o contrato na lista).

## hc-2026.10.21-1 (21/10/2026) — hapvida-check 50ed6cd + lifetools 2bf9c11 · reverter: hc-2026.10.20-2
- Retenção do pós-vendas (abaixo) e detalhe de comissão diário.
- 21/10 (André): `comissoes_detalhe` volta a ser coletada todo dia no /run das 08h (mês atual e anterior, 4 códigos,
  relatório sintético sem PII). Estava parada em 11/09 (só tinha rodado à mão em 17/09). Recarga 09–10/2026 feita e
  conferida: soma por lote = valor do pedido, ao centavo. Lotes pequenos do 05FK sem relatório sintético ficam sem detalhe.
- 21/10 (André): toda mensagem que o vigia/ciclo mandaria ao pós-vendas ou gestor vai para o André, com "retida — era para X".
  Liberar com POS_VENDAS_RETIDO=0 no ambiente do hapvida-check.

## hc-2026.10.20-2 (20/10/2026) — hapvida-check b6b54a4 + lifetools 8d9782f · reverter: hc-2026.10.20-1
- Relatório de fechamento/inclusão: crítica de transferência ainda aberta na Hapvida conta como pendenciada (com o protocolo).

## hc-2026.10.20-1 (20/10/2026) — hapvida-check cc8ad17 + lifetools 55c6432 · reverter: hc-2026.10.19-2
- Motor 0.4.6: pedido cancelado pela Hapvida só com 205/206 e linha que não voltou da Hapvida sobem de novo no reenvio.
- Reenvio automático nunca para calado: qualquer falha da conferência vira retentativa (30 min); carga AUTOMATICO sem
  desfecho há 1h entra sozinha na retentativa (criadas a partir de 20/10); recusa definitiva vira evento REENVIO_RECUSADO.
- Vigia da inclusão: pedido cancelado pela Hapvida roda o motor de novo; no fim manda o relatório de 49 colunas
  (Status + Problema) só com as vidas da inclusão.
