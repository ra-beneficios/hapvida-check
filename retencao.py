"""Retenção das mensagens do pós-vendas (21/10/2026, André): enquanto o processo não estiver 100%, tudo o que o
hapvida-check mandaria pela Cora a outra pessoa (pós-vendas, gestor) vai para o André, com o destinatário original
no começo do texto. Os botões continuam funcionando: o André está entre os autorizados (ciclo_inclusao.decidir).
Liberar: POS_VENDAS_RETIDO=0 no ambiente do hapvida-check."""
import os

ADMIN = os.environ.get("ADMIN_UPN", "andre.aguiar@ra-beneficios.com.br")
RETIDO = os.environ.get("POS_VENDAS_RETIDO", "1") == "1"


def destino(corpo: dict) -> dict:
    """Corpo de chamada à Cora com o upn trocado pelo do André quando a retenção está ligada."""
    upn = (corpo or {}).get("upn")
    if not RETIDO or not upn or upn.lower() == ADMIN.lower():
        return corpo
    novo = dict(corpo, upn=ADMIN)
    if novo.get("texto"):
        novo["texto"] = f"🔒 (retida — era para {upn})\n\n{novo['texto']}"
    print(f"[retencao] mensagem para {upn} redirecionada ao André")
    return novo
