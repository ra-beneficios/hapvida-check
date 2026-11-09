"""Cancelar um pedido de inclusão na Hapvida, como o "Cancelar Pedido" da tela (09/11/2026).

André (09/11): "E por que você não cancela?" — vida marcada "não vai entrar" (ou "Não excluir" na transferência) que já
tinha subido deixava o pedido aberto na Hapvida. A tela "Acompanhar movimentações" tem a ação "Cancelar Pedido" nos
status em digitação (22), digitado (0), pendente (1) e em validação (17, 21); em autorizado (8), processado (9),
ativo (99) e cancelado (2) não tem. Lido no código da tela (main.229a5ef7.js, handleCancelarPedido):

  POST life-screen/control-number/{pedido}/cancel-movement  {birthDate: "aaaa-mm-dd", document: CPF,
                                                             dependentCode: String(dependentControlNumber)}

Cancelar o TITULAR cancela os dependentes do mesmo pedido ("ao prosseguir com o cancelamento do titular, o(s)
dependente(s) vinculado(s) será(ão) cancelado(s)"): só com todos eles em `pode_cancelar`, senão para e diz quem iria.

    python3 pedido_cancelar.py CONTRATO CPF [PEDIDO]               (só lê e mostra o que faria)
    python3 pedido_cancelar.py CONTRATO CPF [PEDIDO] --executar
"""
import json
import re
import sys
import time

import httpx

import pedidos_situacao as ps

CANCELAVEL = ("22", "0", "1", "17", "21")
TIPOS = ps.TIPOS_INCLUSAO


def _cpf(x) -> str:
    return re.sub(r"\D", "", str(x or "")).zfill(11)


def _iso(data: str | None) -> str | None:
    """'dd/mm/aaaa' ou ISO -> 'aaaa-mm-dd' (o que a tela manda)."""
    s = str(data or "")
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        return f"{m[3]}-{m[2]}-{m[1]}"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    return m[0] if m else None


def escolher(movimentos: list[dict], cpf: str, pedido: str | None = None) -> dict | None:
    """A linha do pedido de inclusão deste CPF: a do `pedido` dado, senão a mais recente."""
    linhas = [m for m in movimentos if _cpf(m.get("cpfNumber")) == cpf and m.get("operationDescription") in TIPOS
              and (not pedido or str(m.get("controlNumber")) == str(pedido))]
    return max(linhas, key=lambda m: (ps._data(m.get("typingDate")), int(m.get("controlNumber") or 0)), default=None)


def quem_vai_junto(movimentos: list[dict], linha: dict) -> list[dict]:
    """Titular: as outras vidas do mesmo pedido ainda abertas (o cancelamento leva junto)."""
    if str(linha.get("dependentControlNumber") or "0") != "0":
        return []
    return [m for m in movimentos if str(m.get("controlNumber")) == str(linha.get("controlNumber"))
            and _cpf(m.get("cpfNumber")) != _cpf(linha.get("cpfNumber"))
            and str(m.get("processingStatus") or "") in CANCELAVEL]


def cancelar(contrato: str, cpf: str, pedido: str | None = None, pode_cancelar: list[str] | None = None,
             executar: bool = False) -> dict:
    """-> {pedido, situacao_codigo, ok | ja_cancelado | sem_pedido | nao_cancelavel | bloqueado, ...}"""
    cpf = _cpf(cpf)
    h = ps._sessao(contrato)
    movs = ps._pedidos(h, contrato)
    linha = escolher(movs, cpf, pedido)
    if not linha:
        return {"cpf": cpf, "sem_pedido": True}
    cn, dep_cn = str(linha["controlNumber"]), str(linha.get("dependentControlNumber") or "0")
    st = str(linha.get("processingStatus") or "")
    res = {"cpf": cpf, "pedido": cn, "dependente": dep_cn != "0", "situacao_codigo": st,
           "situacao": linha.get("processingStatusDescription")}
    if st == "2":
        return dict(res, ja_cancelado=True)
    if st not in CANCELAVEL:
        return dict(res, nao_cancelavel=True)
    junto = [m for m in quem_vai_junto(movs, linha) if _cpf(m.get("cpfNumber")) not in {_cpf(c) for c in pode_cancelar or []}]
    if junto:
        return dict(res, bloqueado=[{"nome": m.get("name"), "cpf": _cpf(m.get("cpfNumber"))} for m in junto])
    if dep_cn == "0":
        vida = ps._get(h, f"{ps.LS}/{cn}").get("holder") or {}
    else:
        vida = next((d for d in ps._get(h, f"{ps.LS}/{cn}/dependents").get("dependents") or [] if _cpf(d.get("document")) == cpf), {})
    nasc = _iso(vida.get("birthDate"))
    if not nasc:
        raise RuntimeError(f"não achei a data de nascimento de {cpf} no pedido {cn}")
    res["corpo"] = {"birthDate": nasc, "document": cpf, "dependentCode": dep_cn}
    if not executar:
        return res
    r = httpx.post(f"{ps.LS}/{cn}/cancel-movement", headers={**h, "content-type": "application/json"}, json=res["corpo"],
                   timeout=120)
    res["http"], res["resposta"] = r.status_code, r.text[:500]
    if not r.is_success:
        res["ok"] = False
        return res
    fim = time.time() + 120                    # a tela diz "em alguns instantes, o status será atualizado e cancelado"
    while time.time() < fim:
        time.sleep(15)
        agora = escolher(ps._pedidos(h, contrato, 30), cpf, cn) or {}
        res["situacao_codigo"], res["situacao"] = str(agora.get("processingStatus") or ""), agora.get("processingStatusDescription")
        if res["situacao_codigo"] == "2":
            break
    res["ok"] = True                           # a Hapvida aceitou; situacao_codigo diz se já aparece cancelado
    return res


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    print(json.dumps(cancelar(a[0].upper(), a[1], a[2] if len(a) > 2 else None, executar="--executar" in sys.argv),
                     ensure_ascii=False, indent=1, default=str))
