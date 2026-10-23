# Odonto Premium no Legado (SIGO) — ferramentas

Usadas em 22–23/10/2026 para fechar os Odonto Premium do HC2 (2M2DX, S7HNK, SY8MI, QTYQH, GRKKH, VKTX1...).
Odonto Premium só existe para quem tem Saúde ativo no contrato par. Todos rodam DENTRO do hapvida-check, pelo stdin:

    docker exec -i [-e VAR=...] -w /app hapvida-check python3 - ARGS < odonto_legado/<script>.py

Saída fica em `/tmp/odo/<COD>/` no container. Copiar para fora com `docker cp` antes de publicar/reiniciar o container.
Enviar qualquer coisa ao Legado só com OK do André (pré-cancelamento, formulário, TXT).

| Script | O que faz |
|---|---|
| `precancelar.py` | Pré-cancelamento (CancelamentoContratoCodigo / CancelamentoDependenteCodigo), motivo 50 com questionário A/N/N, sem e-mail. Env `ACOES` = JSON `[[tipo, cpf, nome, cpf_titular], ...]`; args `COD ver\|efetivar`. O Legado só efetiva no 1º dia do ciclo seguinte: pós-vendas pede o IMEDIATO. |
| `gerar_lote.py` + `lote_formulario_tpl.py` | Inclusão por formulário (titular + dependentes). `gerar_lote.py COD fam.json` gera `lote_<COD>.py` com as famílias embutidas; rodar esse arquivo no container. Dependente de titular JÁ ativo não entra por formulário (exige código numérico) → TXT. |
| `montar_familias.py` | Monta `fam_<COD>_novas.json` (formato do lote) a partir do censo Full do portal, para titulares fora do Odonto. |
| `montar_txt.py` | Monta o TXT 829 (plano 9732) dos dependentes cujo titular já está ativo no Odonto. 120: 03 cônjuge, 04 filho, 06 enteado; cônjuge exige data de casamento; 129 = município 6 dígitos (`munic.csv`). |
| `troca_upload.py` | Envia um arquivo pela Troca de Arquivos (login CNPJ + senha; SY8MI usa `senha_troca`). Env `ARQ_B64`; args `COD nome_do_arquivo`. |
| `cadastro_portal.py` | SÓ LEITURA no portal moderno: `beneficiaries?search` → `beneficiaries/{code}` (+ titular). Não traz endereço. |

Entrada comum (`alvo.json`, no diretório atual): lista de `{p: Odonto destino, s: Saúde par, cpf, nome, tit, tipo, tit_atv, tit_pend}`.

Pegadinhas:
- Pedido feito pela TELA do portal não tem linha 829 e o `beneficiaries/{code}` não tem endereço: a fonte é o censo Full fresco
  (`scripts_cowork/patch2_modalidade/baixar_full_portal.py <mãe>`). Avisar a sessão da Saúde antes: o login da Cora é compartilhado.
- Usuario do 2M2DX/S7HNK é enorme: `goto(..., timeout=600000, wait_until="domcontentloaded")`.
- Na janela fechada do mês, inclusão e cancelamento caem no 1º dia do mês seguinte.
