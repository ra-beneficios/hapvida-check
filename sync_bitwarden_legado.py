#!/usr/bin/env python3
"""Copia as credenciais do Legado (portal antigo webhap) do Bitwarden pro cofre
de maquina, segredo `hapvida-legado` = {CODIGO: {"senha", "cnpj"}}.

Roda no HOST (o CLI `bw` mora em ~/.local/bin, fora do container), antes do
faturamento_mensal. O Andre cadastra/edita no Bitwarden: 1 item por contrato,
com a URL webNewBoletoEmpresa.login, senha no campo de senha e campos
personalizados pCodigo (ou o codigo no nome do item) e CNPJ.

Login no Bitwarden pela API key pessoal guardada em `bitwarden-api`
{client_id, client_secret, senha_mestra}. Nunca imprime segredo: so' os codigos.
Se o Bitwarden falhar, o segredo anterior fica como esta (faturamento segue
com a ultima copia boa).
"""
import json
import os
import re
import subprocess
import sys
import tempfile

ARQ = os.environ.get("SECRETS_FILE", "/opt/hapvida-check/secrets/portais.json")
BW = os.path.expanduser("~/.local/bin/bw")
URL_BOLETO = "https://webhap.hapvida.com.br/pls/webhap/webNewBoletoEmpresa.login"


def main() -> None:
    with open(ARQ, encoding="utf-8") as f:
        dados = json.load(f)
    s = dados["bitwarden-api"]
    env = dict(os.environ, BW_CLIENTID=s["client_id"], BW_CLIENTSECRET=s["client_secret"], BW_PASSWORD=s["senha_mestra"])
    run = lambda *a: subprocess.run([BW, *a], env=env, capture_output=True, text=True, timeout=120)

    if json.loads(run("status").stdout or "{}").get("status") == "unauthenticated":
        r = run("login", "--apikey", "--nointeraction")
        if r.returncode:
            sys.exit(f"bitwarden: login falhou ({r.stderr.strip()[:200]})")
    r = run("unlock", "--passwordenv", "BW_PASSWORD", "--raw", "--nointeraction")
    if r.returncode or not r.stdout.strip():
        sys.exit(f"bitwarden: unlock falhou ({r.stderr.strip()[:200]})")
    env["BW_SESSION"] = r.stdout.strip()
    try:
        run("sync")
        itens = json.loads(run("list", "items", "--url", URL_BOLETO).stdout or "[]")
    finally:
        run("lock")

    legado, problemas = {}, []
    for it in itens:
        campos = {x["name"]: (x.get("value") or "").strip() for x in it.get("fields") or []}
        cod = (campos.get("pCodigo") or campos.get("pCodigoEmpresa") or "").upper()
        if not cod:
            m = re.search(r"\b([A-Z0-9]{5})\b", it["name"].upper())
            cod = m.group(1) if m else ""
        senha = (it.get("login") or {}).get("password") or campos.get("pSenha")
        if not cod or not senha:
            problemas.append(f"'{it['name']}' sem código ou senha")
            continue
        if cod in legado:
            problemas.append(f"{cod} em mais de um item - usando o primeiro")
            continue
        legado[cod] = {"senha": senha, "cnpj": re.sub(r"\D", "", campos.get("CNPJ", "")).zfill(14)}

    if not legado:
        sys.exit("bitwarden: nenhum item do Legado encontrado - segredo anterior mantido")
    dados["hapvida-legado"] = legado
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(ARQ))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, ARQ)
    print(f"bitwarden: {len(legado)} contrato(s) do Legado: {', '.join(sorted(legado))}"
          + (f" | {'; '.join(problemas)}" if problemas else ""))


if __name__ == "__main__":
    main()
