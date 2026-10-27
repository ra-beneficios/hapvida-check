# CHANGELOG — hapvida-check

Uma entrada por publicação (`infra/deploy/publicar_hapvida_check.sh` no lifetools). Reverter: `--reverter <tag anterior>`.

## hc-2026.10.27-4 (27/10/2026 20:31, com o André) — hapvida-check cd9778e + lifetools f6a94a4 · reverter: hc-2026.10.27-3
- Motor 0.4.12 / catálogo 1.6.1 — obra 5 (um motor só): "titular vizinho" (dependente sem vínculo logo abaixo do titular de mesmo sobrenome) passa do lote.py para a porta e o reenvio; lote.py, lote_executar.py e lote_relatorio.py removidos. Base de vidas: importação grava sexo M/F (1/3 do layout), 146 registros corrigidos. API lifetools reconstruída junto.

## hc-2026.10.27-3 (27/10/2026 20:07, dia de faturamento com o André) — hapvida-check 114fbd7 + lifetools 24e8456 · reverter: hc-2026.10.27-2
- Motor 0.4.11 / catálogo 1.6.0 — bateria do histórico do portal (04–10/2026, 2.482 críticas): pós-vendas recebe menos. Decisões do André: estado civil vazio sem casamento = solteiro; filho com idade de cônjuge = cônjuge. Porta: estado civil, titular com cônjuge = casado, filho↔cônjuge, cônjuge sem data ganha a provisória, 132c do titular. Reenvio: titular <14 e nascimento pela Receita sem humano, mãe vazia pela Receita, cancelada sem mensagem reenvia, "FEITO INCLUSAO SACCAD" não reenvia, 6 textos novos. 115 críticas reais viraram teste. API lifetools reconstruída junto.

## hc-2026.10.27-2 (27/10/2026, com o André) — hapvida-check 4f152b6 + lifetools 0fb74ce · reverter: hc-2026.10.27-1
- Motor 0.4.10 (só o número: a regra de município/CPF da YYUQ8 tinha subido na -1 como 0.4.9). Scripts avulsos do OUT26 guardados em `historico/out26/` (não usar).

## hc-2026.10.27-1 (27/10/2026, dia de faturamento com o André) — hapvida-check e03780b + lifetools 55d0c4a · reverter: hc-pre-processo-202610271503
- Faturamento: Relatório de Envios no formato aprovado em OUT26 (`montar_relatorio`: tabela azul, enviados e pendentes, linguagem de negócio, sem CSV); zip único acima de 3MB e teto de 10MB (Graph aceitou 8MB; UTC 4,8MB ficou bloqueada à toa na 1ª rodada NOV26); sessão trocada por outra rodada da mesma conta reseleciona o contrato (KGBQY × YYUQ8); lista de ativos pelo "Contrato atual" por contrato (29 contratos em ~1,5 min, antes horas pelo Full da mãe).
- Linha de base travada: `tests/teste_faturamento_linha_base.py` roda na publicação e bloqueia se quebrar.
- Motor (lifetools fix/motor-municipio-cpf-1026, aprovado): município pelo CEP e CPF de outra pessoa vira pergunta (YYUQ8 26/10). API lifetools reconstruída junto. SQL 184 (Odonto Puro sem fatura) commitado.

## hc-2026.10.23-4 (23/10/2026) — hapvida-check (compose consertado) + lifetools 57f1ba8 · reverter: hc-2026.10.23-3
- Segredos: Bitwarden é o cofre único. A cópia que o container lê é gerada de hora em hora por `infra/secrets/bitwarden_para_cofre.py` (lifetools); saem `sync_bitwarden_legado/portal.py` e o backend GCP do `secrets_store` (biblioteca do Google fora da imagem). Tentativa anterior se reverteu sozinha (YAML do compose quebrado por um sed meu).

## hc-2026.10.23-3 (23/10/2026) — hapvida-check dde5d48 + lifetools effb430 · reverter: hc-2026.10.23-2
- Cadastro único (sessão C, SQL 183): lista do Legado, contratos sem faturamento e mães saem do banco (`cadastro.py`), não mais de `empresas_hapvida.xlsx` nem de listas no código; e-mails do envio só de `contatos_faturamento` (menu Empresas da intranet), a planilha `empresas_contato_email.xlsx` deixou de ser copiada por cima. Escopo conferido igual ao anterior (17 Legado, 34 grupos OUT26).

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
