# Levantamento de pendências/críticas Hapvida (todos os contratos) — 26/10/2026

Só leitura. Planilha única: 49 colunas de inclusão + Status + Descrição (+ Contrato, Origem, Movimentação). Só o que NÃO está resolvido.

1. Moderno Saúde (login Cora — nunca junto com outra coleta do portal moderno):
   `levantamento/coletar_moderno.sh LOG <códigos SAUDE/MODERNO/ATIVO>` (fechar_contrato coletar --desde 2026-09-01)
2. Legado Saúde + Odonto Premium (cofre hapvida-legado, pode rodar junto):
   `docker exec -i -w /app hapvida-check python3 - <códigos> < levantamento/coletar_pendencias_legado.py`
   (CriticaCadastral→Rel até 90 dias, pr_relPedidoPendente, Usuario = ativos) → /dados_motor/_legado_pendencias/<COD>/
3. Planilha: `docker exec -i -w /lifetools_service -e ODONTO=<premium,...> hapvida-check python3 - <moderno,...> < levantamento/planilha_pendencias.py`
   → /projeto_ra/movimentacoes/fechamento/LEVANTAMENTO_PENDENCIAS_<AAAAMMDD>/

Legado: 9WLZU e 9GBKA sem senha no cofre (26/10). "MOVIMENTACAO CRITICADA" = envelope; "existe pendência vinculada" = cascata da família.
