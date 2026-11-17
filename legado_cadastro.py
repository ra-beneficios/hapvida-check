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


def _sessao(codigo: str) -> tuple[str, str]:
    """Login do robô no Legado (o captcha só barra o envio do formulário) -> (base da URL, pIdSessao)."""
    import odonto_premium
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            menu = odonto_premium._login(b.new_page(), codigo)
        finally:
            b.close()
    return menu.split("webNewCadastroUsuario.")[0], re.search(r"pIdSessao=([^&\"']+)", menu).group(1)


def _consultar(chaves: list[str], ajax: str, param: str, ler, codigo: str) -> dict:
    if not chaves:
        return {}
    base, sessao = _sessao(codigo)
    out = {}
    with httpx.Client(timeout=60) as cli:
        for k in chaves:
            r = cli.get(f"{base}webNewCadastroUsuario.{ajax}?pIdSessao={sessao}&{param}={k}")
            r.raise_for_status()
            out[k] = ler(r.content.decode("latin-1"))
    return out


def _digitos(xs, n: int) -> list[str]:
    return sorted({re.sub(r"\D", "", x or "").zfill(n) for x in xs if re.sub(r"\D", "", x or "")})[:MAX_CPFS]


def consultar(cpfs: list[str], codigo: str = CODIGO) -> dict[str, dict | None]:
    """-> {cpf: {nome, nascimento dd/mm/aaaa, mae, higienizado} | None (a base não tem o CPF)}. SessaoLegado se recusar."""
    return _consultar(_digitos(cpfs, 11), "AjaxRetornaDadosCpfNeoway", "pCpf", ler_resposta, codigo)


def ler_cep(texto: str) -> dict | None:
    """AjaxCepConsulta -> {cidade, uf, bairro, logradouro} (o que a ficha do Legado preenche pelo CEP) ou None."""
    c = next(iter((json.loads(texto or "{}") or {}).get("cep") or []), None)
    if not c or not (c.get("cidade") or "").strip() or not (c.get("uf") or "").strip():
        return None
    return {"cidade": " ".join(c["cidade"].split()).upper(), "uf": c["uf"].strip().upper(),
            "bairro": " ".join((c.get("bairro") or "").split()).upper() or None,
            "logradouro": " ".join((c.get("nm_logradouro") or "").split()).upper() or None}


def ceps(lista: list[str], codigo: str = CODIGO) -> dict[str, dict | None]:
    """17/11/2026 (André, MOV-36: "salva essa inteligência no motor"): a cidade de cada CEP pela base da Hapvida — o
    município da planilha (129) vinha de outra cidade (Ramon: CEP de Itaboraí com Vitória/ES) e o Legado recusava
    "040-Cep endereço não é válido". -> {cep: {cidade, uf, ...} | None}"""
    return _consultar([c for c in _digitos(lista, 8) if c.strip("0")], "AjaxCepConsulta", "pCep", ler_cep, codigo)


if __name__ == "__main__":
    print(json.dumps(consultar(sys.argv[1:]), ensure_ascii=False, indent=1))
