#!/usr/bin/env bash
# Levantamento 26/10: coletar (só leitura) desde 01/09 em todos os Saúde Moderno ativos. Para antes das 21:50 (coleta 22h).
LOG=$1; shift
for ct in "$@"; do
  if [ "$(date +%H%M)" -ge 2150 ]; then echo "$(date +%T) PAROU antes de $ct (janela das 22h)"; break; fi
  echo "$(date +%T) >>> $ct"
  timeout 1800 docker exec -w /lifetools_service hapvida-check python3 -m saneamento.ferramentas.fechar_contrato coletar "$ct" --desde 2026-09-01 2>&1 | tail -3
done
echo "$(date +%T) FIM"
