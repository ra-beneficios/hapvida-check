#!/usr/bin/env python3
"""#63 - fecha 0KFXW (mae grande) rodando sozinho, sem supervisao.

So extracao + dry-run (rollback automatico) - NUNCA commita nada de
verdade. Dispara gerar_relatorio() UMA SO VEZ (preocupacao de
concorrencia do #59/#63 Passo 2 - nao re-dispara geracao, so re-poll do
mesmo status-report). Loga cada checagem num arquivo simples pra
acompanhamento mesmo se o processo cair no meio.

Janelas de paciencia (checkpoints cumulativos, nao re-disparos):
20min -> 50min -> 90min -> 140min -> 200min (total). Se aparecer erro
real (nao so "ainda processando"), para na hora e registra o erro -
nao conta como uma das janelas de timeout.
"""
import json
import sys
import time
from datetime import datetime

sys.path.insert(0, "/app")

import httpx
from google.cloud import secretmanager
from playwright.sync_api import sync_playwright

import censo_legado
import censo_saude

PROJECT = "lifetools-ra"
LOG_PATH = "/app/fechar_0kfxw.log"
RESULTADO_PATH = "/app/fechar_0kfxw_resultado.json"

# checkpoints cumulativos em segundos (20,50,90,140,200 min)
JANELAS_MIN = [20, 50, 90, 140, 200]
JANELAS_SEG = [m * 60 for m in JANELAS_MIN]
POLL_INTERVAL_SEG = 15


def log(msg):
    linha = f"[{datetime.now().isoformat(timespec='seconds')}] {msg}"
    print(linha, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(linha + "\n")


def get_creds(secret_id):
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{PROJECT}/secrets/{secret_id}/versions/latest"
    resp = client.access_secret_version(request={"name": name})
    return json.loads(resp.payload.data.decode("utf-8"))


def main():
    resultado = {"inicio": datetime.now().isoformat(), "janelas": [], "status_final": None}

    log("=== #63 - Fechar 0KFXW - inicio ===")

    try:
        creds = get_creds("hapvida-portal-saude")
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            try:
                auth = censo_saude.login_saude(page, creds["usuario"], creds["senha"])
            finally:
                browser.close()
        log("Login OK, Authorization + subscription-key capturados.")
    except Exception as e:
        log(f"ERRO no login - parando, nao conta como tentativa de timeout: {e}")
        resultado["status_final"] = "erro_login"
        resultado["erro"] = str(e)
        _salvar(resultado)
        return

    try:
        censo_saude.criar_sessao_empresa(auth, "0KFXW")
        log("create-session OK pra 0KFXW.")
        censo_saude.gerar_relatorio(auth, "0KFXW", "Full")
        log("gerar_relatorio disparado 1x (type=Full) - NAO vou disparar de novo, so re-poll.")
    except Exception as e:
        log(f"ERRO ao disparar geracao - parando, nao conta como tentativa de timeout: {e}")
        resultado["status_final"] = "erro_disparo"
        resultado["erro"] = str(e)
        _salvar(resultado)
        return

    url_status = f"{censo_saude.API_BASE}/beneficiaries/status-report?type=Full"
    inicio_poll = time.time()
    download_link = None
    ultimo_status = None
    janela_idx = 0

    while janela_idx < len(JANELAS_SEG):
        limite_janela = JANELAS_SEG[janela_idx]
        log(f"--- Janela {janela_idx + 1}/{len(JANELAS_SEG)}: paciencia ate {JANELAS_MIN[janela_idx]} min (acumulado) ---")

        while time.time() - inicio_poll < limite_janela:
            try:
                r = httpx.get(url_status, headers=censo_saude._headers_http(auth), timeout=30)
                r.raise_for_status()
                body = r.json()
                status = body.get("content", {}).get("status")
            except Exception as e:
                log(f"ERRO HTTP/parse no poll (nao e timeout de processamento) - parando: {e}")
                resultado["status_final"] = "erro_poll"
                resultado["erro"] = str(e)
                resultado["ultimo_status_conhecido"] = ultimo_status
                _salvar(resultado)
                return

            decorrido = int(time.time() - inicio_poll)
            if status != ultimo_status:
                log(f"status mudou pra '{status}' aos {decorrido}s")
                ultimo_status = status

            if status == "Processed":
                download_link = body.get("content", {}).get("downloadLink")
                if not download_link:
                    log(f"ERRO: status Processed mas sem downloadLink: {body}")
                    resultado["status_final"] = "erro_sem_download_link"
                    _salvar(resultado)
                    return
                log(f"PROCESSADO com sucesso aos {decorrido}s ({decorrido/60:.1f} min).")
                resultado["tempo_total_segundos"] = decorrido
                break

            time.sleep(POLL_INTERVAL_SEG)

        if download_link:
            break

        resultado["janelas"].append({
            "janela": janela_idx + 1,
            "limite_min": JANELAS_MIN[janela_idx],
            "ultimo_status": ultimo_status,
        })
        log(f"Janela {janela_idx + 1} esgotada sem sucesso (ultimo status: {ultimo_status}).")
        janela_idx += 1

    if not download_link:
        log(f"ESGOTOU as {len(JANELAS_SEG)} janelas ({JANELAS_MIN[-1]} min no total) sem sucesso. Parando.")
        resultado["status_final"] = "timeout_esgotado"
        resultado["ultimo_status_conhecido"] = ultimo_status
        _salvar(resultado)
        return

    # download + dry-run + limpeza
    try:
        r = httpx.get(download_link, timeout=60)
        r.raise_for_status()
        csv_bytes = r.content
        log(f"CSV baixado, tamanho: {len(csv_bytes)} bytes.")

        dry_run = censo_legado._rodar_loader_dry_run(csv_bytes)
        log(f"Dry-run do loader: {json.dumps(dry_run, ensure_ascii=False)}")

        resultado["status_final"] = "sucesso"
        resultado["csv_len"] = len(csv_bytes)
        resultado["dry_run"] = dry_run
        # csv_bytes sai de escopo aqui, nunca gravado em disco em lugar nenhum
    except Exception as e:
        log(f"ERRO no download/dry-run pos-processamento: {e}")
        resultado["status_final"] = "erro_pos_processamento"
        resultado["erro"] = str(e)

    _salvar(resultado)
    log("=== #63 - Fechar 0KFXW - fim ===")


def _salvar(resultado):
    resultado["fim"] = datetime.now().isoformat()
    with open(RESULTADO_PATH, "w") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
