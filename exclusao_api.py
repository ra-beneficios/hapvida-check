"""
Motor de Exclusao - lado do portal (o motor em si mora no repo lifetools, `motor_exclusao/`, montado so-leitura
em /motor). Aqui so' o que precisa do portal: baixar as fontes da Hapvida (censo Current + pedidos de
cancelamento ja registrados) e subir o TXT aprovado. Quem decide e' o motor; quem aprova e envia e' o solicitante,
clicando na Cora.

Achados que moldaram isto (lote EPC10/2XP6W, 02-03/10/2026):
- o censo nao mostra pedido de cancelamento aberto -> buscar tambem a lista de pedidos (operation-types);
- titular e dependente no mesmo arquivo: a Hapvida pode processar o dependente antes e recusar o titular
  ("ja existe pedido de pre-cancelamento") -> envio em 2 ondas; a dos dependentes so' depois da dos titulares
  PROCESSADA (nao basta enviada);
- a fila da Hapvida levou de ~3h a ~9h30 para processar.
"""
import base64
import fcntl
import os
import sys
import time
from datetime import date, datetime, timedelta

import httpx
from playwright.sync_api import sync_playwright

import censo_saude
import movimentacao_saude

sys.path.insert(0, os.environ.get("MOTOR_EXCLUSAO_PATH", "/motor"))
from motor_exclusao import servico  # noqa: E402
from motor_exclusao.motor import PendenciaAprovacao  # noqa: E402

API = "https://api.hapvida.com.br/b2b/api/v3"
STATUS_PROCESSADO = {"FileProcessed", "FileProcessedWithReview", "ProcessFileWithPendency"}


def _pastas() -> servico.Pastas:
    return servico.Pastas(os.environ.get("EXCLUSAO_LOTES_DIR", "/projeto_ra/movimentacoes/exclusao"),
                          os.environ.get("EXCLUSAO_PRIVADO_DIR", "/dados_exclusao"))


def _login(contrato: str) -> dict:
    creds = movimentacao_saude._credenciais_por_contrato(os.environ.get("GCP_PROJECT"), contrato)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            return censo_saude.login_saude(browser.new_page(), creds["usuario"], creds["senha"])
        finally:
            browser.close()


def _pedidos_cancelamento(auth: dict, contrato: str) -> dict:
    """Pedidos de cancelamento/exclusao dos ultimos 180 dias (limite da API)."""
    fim = date.today()
    ini = fim - timedelta(days=179)
    itens, off = [], 0
    while True:
        r = httpx.get(f"{API}/movements/status/partner-company/{contrato}/operation-types",
                      headers=censo_saude._headers_http(auth), timeout=60,
                      params={"start-date": ini.isoformat(), "end-date": fim.isoformat(),
                              "operation-types": "CancellationExclusion", "offset": off, "limit": 100})
        r.raise_for_status()
        cont = r.json().get("content") or {}
        lote = cont.get("movements") or []
        itens += lote
        off += len(lote)
        if not lote or off >= int(cont.get("total") or 0):
            break
    return {"contrato": contrato, "capturado_em": datetime.now().astimezone().isoformat(), "itens": itens}


def _fontes(contrato: str) -> dict:
    """Censo Current + pedidos, mesma sessao. Contrato que nao abre sessao na API moderna (legado) -> erro claro."""
    auth = _login(contrato)
    try:
        censo_saude.criar_sessao_empresa(auth, contrato)
    except httpx.HTTPStatusError as e:
        raise PendenciaAprovacao(f"o contrato {contrato} nao abre no portal moderno (legado?) - exclusao dele "
                                 f"ainda e' manual") from e
    capturado = datetime.now().astimezone().isoformat()
    csv_bytes = censo_saude.baixar_relatorio(contrato, auth, tipo="Current")
    censo = f"# capturado_em={capturado} contrato={contrato} tipo=Current\n" + csv_bytes.decode("utf-8-sig", errors="replace")
    return {"censo_csv": censo, "pedidos": _pedidos_cancelamento(auth, contrato)}


def analisar(planilha_base64: str, nome_arquivo: str, solicitante_upn: str) -> dict:
    conteudo = base64.b64decode(planilha_base64)
    contratos = servico.contratos_da_planilha(conteudo, nome_arquivo)
    if not contratos:
        raise PendenciaAprovacao("nao achei contrato nem codigo da operadora (708) na planilha")
    fontes = {c: _fontes(c) for c in contratos}
    return servico.analisar_planilha(conteudo, nome_arquivo, solicitante_upn, fontes, _pastas())


def aprovar(lote: str, aprovador_upn: str, checklist_ok: bool, decisoes: dict) -> dict:
    return servico.aprovar(lote, aprovador_upn, checklist_ok, decisoes or {}, _pastas())


def _status_protocolo(contrato: str, protocolo: str) -> str | None:
    dados = movimentacao_saude.checar_status_movimentacoes(os.environ.get("GCP_PROJECT"), contrato, limit=20)
    for m in (dados.get("content") or {}).get("movements") or []:
        if str(m.get("controlCode")) == str(protocolo):
            return m.get("status")
    return None


def _achar_protocolo(contrato: str, nome_arquivo: str, tentativas: int = 3) -> str | None:
    base = os.path.splitext(nome_arquivo)[0]
    for _ in range(tentativas):
        dados = movimentacao_saude.checar_status_movimentacoes(os.environ.get("GCP_PROJECT"), contrato, limit=10)
        for m in (dados.get("content") or {}).get("movements") or []:
            if (m.get("uploadName") or "").startswith(base):
                return str(m.get("controlCode"))
        time.sleep(20)
    return None


def enviar(lote: str, onda: str, hash_txt: str, solicitante_upn: str) -> dict:
    """Um envio por lote de cada vez (13/10: a onda 1 sai sozinha no "Aprovar"; dois cliques quase juntos
    passariam os dois pela checagem de "ja enviado" antes de qualquer um registrar o envio)."""
    pastas = _pastas()
    with open(os.path.join(pastas.lotes, os.path.basename(lote), ".envio.lock"), "w") as trava:
        try:
            fcntl.flock(trava, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PendenciaAprovacao("ja tem um envio deste lote em andamento - aguarde o resultado dele")
        return _enviar(lote, onda, hash_txt, solicitante_upn, pastas)


def _enviar(lote: str, onda: str, hash_txt: str, solicitante_upn: str, pastas: servico.Pastas) -> dict:
    alvo = servico.preparar_envio(lote, onda, hash_txt, solicitante_upn, pastas)
    if onda == "dependentes":
        estado = servico.ler_estado(lote, pastas)
        tit = [e for e in estado["envios"] if e["onda"] == "titulares" and e["contrato"] == alvo["contrato"]]
        for e in tit:
            st = _status_protocolo(alvo["contrato"], e.get("protocolo")) if e.get("protocolo") else None
            if st not in STATUS_PROCESSADO:
                raise PendenciaAprovacao(
                    f"a onda dos titulares (protocolo {e.get('protocolo') or '?'}) ainda nao foi processada pela "
                    f"Hapvida (status {st or 'desconhecido'}) - os dependentes so' podem ir depois")
    resultado = movimentacao_saude.run_inclusao(os.environ.get("GCP_PROJECT"), alvo["contrato"], alvo["caminho"])
    protocolo = _achar_protocolo(alvo["contrato"], alvo["arquivo"]) if resultado.get("status") in (200, None) else None
    envio = {"onda": onda, "contrato": alvo["contrato"], "hash": hash_txt, "arquivo": alvo["arquivo"],
             "linhas": alvo["linhas"], "status": resultado.get("status"), "protocolo": protocolo,
             "por": solicitante_upn, "em": datetime.now().astimezone().isoformat()}
    if protocolo or resultado.get("status") == 200:
        servico.registrar_envio(lote, envio, pastas)
    return {**envio, "upload": {k: v for k, v in resultado.items() if k != "body"}}


def datas_do_envio(nome_arquivo: str) -> str:
    """13/10 (pedido do Andre, C-MMIND): data PEDIDA x data que a Hapvida REGISTROU para um TXT de exclusao ja
    processado -> bloco de texto para a mensagem final ("" se o arquivo nao e' de lote ou a consulta falhar)."""
    achado = servico.contrato_do_envio(nome_arquivo, _pastas())
    if not achado:
        return ""
    _, contrato = achado
    auth = _login(contrato)
    censo_saude.criar_sessao_empresa(auth, contrato)
    return servico.texto_datas(servico.datas_do_envio(nome_arquivo, _pedidos_cancelamento(auth, contrato), _pastas()))


def registrar_recusas(nome_arquivo: str, vidas_criticadas: list[dict]) -> dict | None:
    """Chamado pelo verificar_acompanhamentos quando um TXT de exclusao termina de processar: grava no lote quais
    vidas a Hapvida recusou (pela linha 701 do nosso arquivo). Best-effort - quem chama trata excecao."""
    recusas = [{"sequencial": v.get("sequencial"), "mensagens": v.get("mensagens") or []}
               for v in vidas_criticadas if v.get("status") not in ("Processed", "Processing")]
    return servico.marcar_recusados(nome_arquivo, recusas, _pastas())
