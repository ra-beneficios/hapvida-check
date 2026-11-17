"""Base certificada da Hapvida por CPF, pelo Legado (17/11/2026). Só leitura.

André (MOV-15 l.107/136): "as duas decisões dizem que os nomes estão diferentes da Hapvida. Se estão, quais vêm na
Hapvida? É possível utilizá-los e retirar a pendência?" A crítica "Nome da Mãe / Nome Beneficiário / Data de
Nascimento tem divergência com dados já certificados" não diz o valor, e o portal moderno devolve o CPF "higienizado" em
branco (life-data isSanitized S). O Legado preenche a ficha de inclusão sozinho ao digitar o CPF — é a mesma base:

  GET /pls/webhap/webNewCadastroUsuario.AjaxRetornaDadosCpfNeoway?pIdSessao=...&pCpf=CPF
  -> {"cpf": [{"nm_beneficiario", "dt_nascimento" (dd/mm/aaaa), "nm_mae", "cpf_higienizado"}]}

O login do robô no Legado passa (o reCAPTCHA só barra o envio do formulário de inclusão). Em 17/11 os 7 CPFs das
pendências "dado certificado" dos chamados abertos responderam; em 6 o valor difere exatamente no campo recusado.

    python3 legado_cadastro.py CPF [CPF ...]
"""
import json
import os
import re
import sys

import httpx

CODIGO = os.getenv("LEGADO_CADASTRO_CODIGO", "2M2DX")      # qualquer código do Legado com senha no cofre serve
MAX_CPFS = 200


def ler_resposta(texto: str) -> dict | None:
    """A resposta da consulta -> {nome, nascimento, mae, higienizado} | None (sem nome = a base não tem o CPF)."""
    achado = next(iter((json.loads(texto or "{}") or {}).get("cpf") or []), None)
    if not achado or not (achado.get("nm_beneficiario") or "").strip():
        return None
    return {"nome": " ".join(achado["nm_beneficiario"].split()),
            "nascimento": (achado.get("dt_nascimento") or "").strip() or None,
            "mae": " ".join((achado.get("nm_mae") or "").split()) or None,
            "higienizado": str(achado.get("cpf_higienizado") or "").upper() == "S"}


def consultar(cpfs: list[str], codigo: str = CODIGO) -> dict[str, dict | None]:
    """-> {cpf: {nome, nascimento dd/mm/aaaa, mae, higienizado} | None (a base não tem o CPF)}. SessaoLegado se recusar."""
    import odonto_premium
    from playwright.sync_api import sync_playwright
    cpfs = sorted({re.sub(r"\D", "", c or "").zfill(11) for c in cpfs if re.sub(r"\D", "", c or "")})[:MAX_CPFS]
    if not cpfs:
        return {}
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            menu = odonto_premium._login(b.new_page(), codigo)
        finally:
            b.close()
    sessao = re.search(r"pIdSessao=([^&\"']+)", menu).group(1)
    base = menu.split("webNewCadastroUsuario.")[0]
    out = {}
    with httpx.Client(timeout=60) as cli:
        for cpf in cpfs:
            r = cli.get(f"{base}webNewCadastroUsuario.AjaxRetornaDadosCpfNeoway?pIdSessao={sessao}&pCpf={cpf}")
            r.raise_for_status()
            out[cpf] = ler_resposta(r.content.decode("latin-1"))
    return out


if __name__ == "__main__":
    print(json.dumps(consultar(sys.argv[1:]), ensure_ascii=False, indent=1))
