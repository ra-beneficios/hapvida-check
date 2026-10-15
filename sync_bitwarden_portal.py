#!/usr/bin/env python3
"""Copia a conta do PORTAL MODERNO (Portal Empresas Saúde) do Bitwarden pro cofre de máquina, segredo
`hapvida-portal-saude` = {usuario, senha, portal}. É a conta que todo o servidor usa (coleta, uploads, vigia,
faturamento), inclusive DAVJV/B6PJX desde 15/10. A conta do Vinicios (`hapvida-portal-saude-marco`) fica de backup.

15/10/2026 (André): a conta do sistema passa a ser a da Cora (master de todos os contratos); a da Carol virou
administradora e fica só pra uso humano. Trocar a conta do sistema = mudar ITEM abaixo, nada mais.
Roda no HOST (o CLI `bw` mora em ~/.local/bin). Nunca imprime segredo. Se o Bitwarden falhar, o segredo anterior
fica como está.
"""
import json
import os
import subprocess
import sys
import tempfile

ARQ = os.environ.get("SECRETS_FILE", "/opt/hapvida-check/secrets/portais.json")
BW = os.path.expanduser("~/.local/bin/bw")
ITEM = os.environ.get("BW_ITEM_PORTAL", "Portal Empresas - Saúde - Cora")
SEGREDO = "hapvida-portal-saude"


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
        itens = [i for i in json.loads(run("list", "items", "--search", ITEM).stdout or "[]") if i.get("name") == ITEM]
    finally:
        run("lock")
    if len(itens) != 1:
        sys.exit(f"bitwarden: esperava 1 item '{ITEM}', achei {len(itens)} — segredo mantido")
    lg = itens[0].get("login") or {}
    if not lg.get("username") or not lg.get("password"):
        sys.exit(f"bitwarden: item '{ITEM}' sem usuário/senha — segredo mantido")
    atual = dados.get(SEGREDO) or {}
    novo = {**atual, "usuario": lg["username"], "senha": lg["password"]}
    if novo == atual:
        print(f"{SEGREDO}: já igual ao Bitwarden ({ITEM})")
        return
    dados[SEGREDO] = novo
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(ARQ))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, ARQ)
    print(f"{SEGREDO}: atualizado a partir do Bitwarden ({ITEM}; usuário {'mudou' if atual.get('usuario') != lg['username'] else 'igual'})")


if __name__ == "__main__":
    main()
