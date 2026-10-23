# CHANGELOG — hapvida-check

Uma entrada por publicação (`infra/deploy/publicar_hapvida_check.sh` no lifetools). Reverter: `--reverter <tag anterior>`.

## hc-2026.10.23-2 (23/10/2026) — hapvida-check 44178fb + lifetools 7931fed · reverter: hc-2026.10.23-1
- pedidos_digitacao (cron 04:00): todos os contratos Saúde ativos do cadastro (antes só com fatura em 60 dias; G5LXS e mais 6 ficavam fora); código fora do login da Cora (9WLZU, ERQ43, ICBJY, Z8TUR) é ignorado sem alerta.

## hc-2026.10.23-1 (23/10/2026) — hapvida-check 2f6a8e5 + lifetools 7931fed · reverter: hc-2026.10.22-1
- fechar_contrato: pedidos de inclusão digitados na TELA do portal entram na coleta e no relatório (movements-list só tem arquivos; 43 na 4DFEV ficavam fora — Fernanda, Ana Lara). Ativo pela tela não vai pro TXT do Odonto (sem linha de 829). Conversor da API: estado civil por texto (Divorciado→5 etc.), API reconstruída antes (3f24685).

## hc-2026.10.22-1 (22/10/2026) — hapvida-check 1766132 + lifetools 78f90a8 · reverter: hc-2026.10.21-5
- Motor 0.4.8 (estado civil inválido com data de casamento vira casado, FHMWR) + 0.4.9 (críticas do Legado no catálogo: 499, 372/373, 017/119, 020/022 RG sem órgão/UF, pedido pendente/digitado, 156f outra empresa do grupo). API lifetools reconstruída junto (porta de entrada).

## hc-2026.10.21-5 (21/10/2026) — hapvida-check 2a7c27b + lifetools 3c3b85e · reverter: hc-2026.10.21-4
- Ciclo de inclusão: relatório final um por vez e refeito se sair vazio (8DAC9 inclusão 2 saiu com 0 vidas porque duas finalizações simultâneas trocaram o contrato da sessão).

## hc-2026.10.21-4 (21/10/2026) — hapvida-check 4c081c5 + lifetools 3c3b85e · reverter: hc-2026.10.21-3
- Motor 0.4.7: cidade de CEP único (Tanguá) usa o CEP da agência dos Correios XXXXX-970 que a Hapvida aceita, na consulta ao vivo da porta de entrada e na correção de crítica.

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
