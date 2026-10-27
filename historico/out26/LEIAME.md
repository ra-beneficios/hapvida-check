# OUT26: scripts avulsos do 1º lote real (30/09/2026)

Só histórico. NÃO usar. O motor (`envio_faturamento.py`) absorveu tudo em 27/10/2026:
- `envio_lote_out26.py`: envio manual dos 26 grupos de OUT26 (lista fixa de grupos).
- `relatorio_out26.py`: o Relatório de Envios que o André aprovou. Como o layout ficou só aqui, o NOV26 saiu
  desestruturado. Hoje ele está em `envio_faturamento.montar_relatorio`, e `tests/teste_faturamento_linha_base.py`
  trava esse formato.
