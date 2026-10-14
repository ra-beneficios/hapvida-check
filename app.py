import os

from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

import base64
import tempfile

import boletos_saude
import censo_legado
import censo_saude
import ciclo_criticas
import copart_saude
import documentos_legado
import documentos_saude
import enviar_documentos
import exclusao_api
import movimentacao_saude
import scraper

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run")
def run():
    return scraper.run_all()


@app.post("/run-censo-legado")
def run_censo_legado():
    """Grupo legado (12 Odonto Premium + DAVJV + Z8TUR). Roda em
    dry-run - nao commita nada (ver auto_commit.py, ainda nao ativado)."""
    project = os.environ["GCP_PROJECT"]
    return censo_legado.run_censo_legado(project, censo_legado.CONTRATOS_LEGADO)


@app.post("/run-censo-saude")
def run_censo_saude():
    """Grupo saude (0KFXW/WARQU + coligadas), via API moderna. Roda em
    dry-run - nao commita nada (ver auto_commit.py, ainda nao ativado)."""
    project = os.environ["GCP_PROJECT"]
    creds = scraper.get_credentials(project, "hapvida-portal-saude")
    bruto = censo_saude.run_censo_saude(creds["usuario"], creds["senha"])

    resultados = []
    for r in bruto["resultados"]:
        if r["status"] != "ok":
            resultados.append(r)
            continue
        dry_run = censo_legado._rodar_loader_dry_run(r["csv_bytes"])
        resultados.append({
            "codigo": r["codigo"],
            "status": "ok",
            "csv_len": r["csv_len"],
            "dry_run": dry_run,
        })
    return {"resultados": resultados}


@app.post("/run-copart-saude")
def run_copart_saude():
    """Grupo saude (0KFXW/WARQU + coligadas) - detalhe por procedimento do Copart, ciclo
    aberto (#106). Grava de verdade em copart_saude_detalhe (banco lifetools) - nao e dry-run,
    o dado some do endpoint da Hapvida depois que fatura, entao precisa persistir aqui."""
    project = os.environ["GCP_PROJECT"]
    creds = scraper.get_credentials(project, "hapvida-portal-saude")
    return copart_saude.run_copart_saude(creds["usuario"], creds["senha"])


@app.post("/run-boletos-saude")
def run_boletos_saude():
    """Grupo saude (0KFXW/WARQU + coligadas) - faturas/boletos via
    billings/partner-company/{codigo}/bills/search (#84). Grava de verdade
    (upsert por billing_number+numero, idempotente) - nao e dry-run."""
    project = os.environ["GCP_PROJECT"]
    creds = scraper.get_credentials(project, "hapvida-portal-saude")
    return boletos_saude.run_boletos_saude(creds["usuario"], creds["senha"])


@app.post("/run-mapear-documentos-saude")
def run_mapear_documentos_saude():
    """Grupo saude - checa disponibilidade de Nota Fiscal e Relatorio Analitico
    (PDF) na fatura mais recente de cada contrato, grava em `faturas`
    (nfe_disponivel, relatorio_analitico_disponivel). So mapeia/registra
    disponibilidade - nao baixa nem envia nada."""
    project = os.environ["GCP_PROJECT"]
    creds = scraper.get_credentials(project, "hapvida-portal-saude")
    return documentos_saude.run_mapear_documentos(creds["usuario"], creds["senha"])


@app.post("/run-davjv-marco")
def run_davjv_marco():
    """DAVJV (#147) - a conta da Carol (hapvida-portal-saude) NAO tem acesso
    a esse contrato (create-session dava 400 Bad Request desde 31/07). A
    conta nova do Marcos (hapvida-portal-saude-marco) tem. Como essa conta
    so' enxerga 12 contratos (11 ja cobertos pela Carol + DAVJV/B6PJX),
    rodar so' DAVJV aqui evita trabalho duplicado com os crons normais.
    Boletos + coparticipacao (B6PJX e' Odonto Puro, sempre 0 faturas - nao
    precisa rodar boletos nele, so' existe pra registro)."""
    import boletos_saude as bs
    import copart_saude as cs
    from playwright.sync_api import sync_playwright
    from censo_saude import login_saude
    from datetime import date

    project = os.environ["GCP_PROJECT"]
    creds = scraper.get_credentials(project, "hapvida-portal-saude-marco")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        try:
            auth = login_saude(page, creds["usuario"], creds["senha"])
        finally:
            browser.close()

    resultado = {}
    try:
        linhas = bs.capturar_boletos_contrato(auth, "DAVJV", "2025-01-01", date.today().replace(year=date.today().year + 1).isoformat())
        resultado["boletos"] = {"faturas": len(linhas), **bs.upsert_faturas(linhas, fonte=f"davjv_marco_{date.today().isoformat()}")}
    except Exception as e:
        resultado["boletos"] = {"erro": str(e)}

    try:
        linhas_copart = cs.capturar_copart_contrato(auth, "DAVJV")
        resultado["copart"] = {"linhas": len(linhas_copart), "gravadas": cs.upsert_copart(linhas_copart)}
    except Exception as e:
        resultado["copart"] = {"erro": str(e)}

    return resultado


class Inclusao(BaseModel):
    codigo_contrato: str
    arquivo_txt_base64: str
    nome_arquivo: str = "movimentacao.txt"


@app.post("/run-inclusao")
def run_inclusao(payload: Inclusao):
    """#150 - Sobe um arquivo de movimentacao (.txt, 829 chars/linha, ja gerado por
    /converter no service lifetools-api) no portal-empresa moderno da Hapvida, contrato
    identificado por codigo_contrato (campo 132a do arquivo). Pensado pra Cora chamar depois
    de POST /converter, com o mesmo arquivo que ele devolveu.

    PARA no 200 OK do upload - nao checa status de processamento (ver notas detalhadas em
    movimentacao_saude.py sobre por que checar logo depois e' enganoso). O resultado real
    (processado / com criticas) so' se sabe horas depois - checar separado, ver
    scripts_cowork/patch2_modalidade/150_checar_status_inclusoes_24h.py no repo lifetools."""
    project = os.environ["GCP_PROJECT"]
    conteudo = base64.b64decode(payload.arquivo_txt_base64)

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
        tmp.write(conteudo)
        caminho_tmp = tmp.name

    try:
        resultado = movimentacao_saude.run_inclusao(project, payload.codigo_contrato.upper(), caminho_tmp)
    finally:
        os.unlink(caminho_tmp)

    return resultado


class SessaoLegado(BaseModel):
    url_nota_fiscal: str | None = None
    url_coparticipacao: str | None = None
    url_status_boleto: str | None = None
    contrato_codigo: str | None = None
    fonte: str = "manual"


class EnvioDocumentos(BaseModel):
    codigo: str
    destinatario: str = "andre.aguiar@ra-beneficios.com.br"
    cc: str = ""
    bcc: str = ""
    enviar_email: bool = True


@app.post("/enviar-documentos-contrato")
def enviar_documentos_contrato(payload: EnvioDocumentos):
    """Grupo Saude - baixa Boleto/Nota Fiscal/Relatorio Analitico da fatura
    mais recente (saude + coparticipacao, se existir) + coparticipacao
    analitico do nosso banco, monta e-mail com tabela de anexos, envia via
    Graph (noreply@ra-beneficios.com.br). Pensado pra Cora chamar.

    DAVJV/B6PJX (#147) NAO usam a conta da Carol - create-session da 400
    Bad Request desde 31/07 (ver /run-davjv-marco). Usam a conta do Marcos
    (hapvida-portal-saude-marco), a unica com acesso a esse contrato -
    achado real 31/08/2026 (#152), so' descoberto de novo na hora de ligar
    o envio de documentos pra esse codigo. Se aparecer 400/erro de sessao
    pra outro codigo novo, o mesmo problema pode se repetir - conferir
    qual conta realmente enxerga o contrato antes de assumir Carol."""
    project = os.environ["GCP_PROJECT"]
    secret_id = "hapvida-portal-saude-marco" if payload.codigo.upper() in ("DAVJV", "B6PJX") else "hapvida-portal-saude"
    creds = scraper.get_credentials(project, secret_id)
    return enviar_documentos.montar_e_enviar(
        payload.codigo.upper(), payload.destinatario, project, creds["usuario"], creds["senha"],
        payload.enviar_email, payload.cc, payload.bcc
    )


@app.post("/processar-sessao-legado")
def processar_sessao_legado(payload: SessaoLegado):
    """Grupo Legado - Andre passa URL(s) de sessao ja autenticada manualmente
    (login bloqueado por captcha, #77 - nunca automatizado aqui). So faz GET
    direto nas URLs recebidas, parseia e grava em faturas_legado /
    copart_legado_detalhe. Nao tenta logar - sessao expirada = erro no resultado.

    url_status_boleto (#146): pagina Bemobi (webNewBoletoEmpresa.obrigacaoListaBemobi)
    - iframe presente = Em Aberto, ausente = Liquidado. Precisa vir com
    contrato_codigo (ex: 'S7HNK') pra saber em qual fatura gravar o status."""
    return documentos_legado.processar_sessao_legado(
        payload.url_nota_fiscal,
        payload.url_coparticipacao,
        payload.url_status_boleto,
        payload.contrato_codigo,
        payload.fonte,
    )


# ── Motor de Exclusao (Cora: menu Operadoras -> Hapvida -> Realizar Procedimento de Exclusao) ──────────────
# 3 passos separados, cada um disparado por um clique do solicitante. Nada e' enviado sem o clique em
# "enviar"; a onda dos dependentes so' depois da dos titulares processada. Ver exclusao_api.py.

class ExclusaoAnalisar(BaseModel):
    planilha_base64: str
    nome_arquivo: str
    solicitante_upn: str


class ExclusaoAprovar(BaseModel):
    lote: str
    aprovador_upn: str
    checklist_ok: bool = False
    decisoes: dict = {}


class ExclusaoEnviar(BaseModel):
    lote: str
    onda: str
    hash: str
    solicitante_upn: str


def _exclusao(fn, *args):
    try:
        return fn(*args)
    except exclusao_api.PendenciaAprovacao as e:
        # regra de negocio (falta decisao, onda fora de ordem, hash diferente...) - 409 com texto pra Cora mostrar
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/exclusao/analisar")
def exclusao_analisar(p: ExclusaoAnalisar):
    return _exclusao(exclusao_api.analisar, p.planilha_base64, p.nome_arquivo, p.solicitante_upn)


@app.post("/exclusao/aprovar")
def exclusao_aprovar(p: ExclusaoAprovar):
    return _exclusao(exclusao_api.aprovar, p.lote, p.aprovador_upn, p.checklist_ok, p.decisoes)


@app.post("/exclusao/enviar")
def exclusao_enviar(p: ExclusaoEnviar):
    return _exclusao(exclusao_api.enviar, p.lote, p.onda, p.hash, p.solicitante_upn)


# ── Ciclo de críticas (08/10/2026) — menu "Analisar críticas" e cartão de aprovação da Cora ──────────────────
class CicloMovimentacoes(BaseModel):
    contrato: str
    data: str  # AAAA-MM-DD


class CicloRodar(BaseModel):
    contrato: str
    controles: list[str]
    solicitante_upn: str


class CicloAprovar(BaseModel):
    execucao_id: int
    escopo: str  # AUTOMATICO | COM_SUGERIDAS
    aprovado_por: str


class CicloDescartar(BaseModel):
    execucao_id: int
    por: str


def _ciclo(fn, *args):
    try:
        return fn(*args)
    except ciclo_criticas.Recusa as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.post("/ciclo/movimentacoes")
def ciclo_movimentacoes(p: CicloMovimentacoes):
    return {"movimentacoes": _ciclo(ciclo_criticas.movimentacoes_do_dia, p.contrato.upper(), p.data)}


def _ciclo_em_segundo_plano(p: CicloRodar) -> None:
    try:
        ciclo_criticas.rodar(p.contrato, p.controles, {"origem": "MENU", "solicitante": p.solicitante_upn, "rodada": 1})
    except ciclo_criticas.Recusa as e:
        ciclo_criticas._cora("/notificar", {"upn": p.solicitante_upn, "texto": f"⚠️ Não analisei: {e}"})
    except Exception as e:
        print(f"[ciclo] erro na análise {p.contrato} {p.controles}: {type(e).__name__}: {e}")
        ciclo_criticas._cora("/notificar", {"upn": p.solicitante_upn,
                                            "texto": "⚠️ A análise de críticas deu erro técnico — avisei o André."})
        ciclo_criticas._cora("/notificar", {"upn": ciclo_criticas.ciclo.ADMIN,
                                            "texto": f"🛠️ Ciclo de críticas falhou ({p.contrato} {p.controles}): {type(e).__name__}"})


@app.post("/ciclo/rodar", status_code=202)
def ciclo_rodar(p: CicloRodar, tarefas: BackgroundTasks):
    if not p.controles:
        raise HTTPException(status_code=409, detail="escolha ao menos uma movimentação")
    tarefas.add_task(_ciclo_em_segundo_plano, p)
    return {"aceito": True}


@app.post("/ciclo/aprovar")
def ciclo_aprovar(p: CicloAprovar):
    if p.escopo not in ("AUTOMATICO", "COM_SUGERIDAS"):
        raise HTTPException(status_code=409, detail="escopo inválido")
    return _ciclo(ciclo_criticas.aprovar, p.execucao_id, p.escopo, p.aprovado_por)


@app.post("/ciclo/descartar")
def ciclo_descartar(p: CicloDescartar):
    return _ciclo(ciclo_criticas.descartar, p.execucao_id, p.por)


# ── Porta de entrada da inclusão (13/10/2026): quem do arquivo já está ativo na Hapvida (consulta ao vivo) ──
class ConsultarCpfs(BaseModel):
    contrato: str
    cpfs: list[str]


@app.post("/inclusao/consultar-cpfs")
def inclusao_consultar_cpfs(p: ConsultarCpfs):
    """Só leitura (GET na API do portal moderno). -> {"certificados": {cpf: [detalhe]}, "consultados": [cpf]}.
    409 quando não há acesso ao contrato pela API (sem credencial/sessão) — quem chama usa a base de vidas."""
    import re as _re
    contrato = p.contrato.strip().upper()
    cpfs = {c for c in p.cpfs if _re.fullmatch(r"\d{11}", c or "")}
    if not _re.fullmatch(r"[0-9A-Z]{5}", contrato) or not cpfs or len(cpfs) > 3000:
        raise HTTPException(status_code=409, detail="contrato ou lista de CPFs inválida")
    try:
        with ciclo_criticas.coleta_portal.sessao(contrato) as (page, headers):
            return ciclo_criticas.coleta_portal.consultar_cpfs(page, headers, contrato, cpfs)
    except Exception as e:
        print(f"[inclusao/consultar-cpfs] {contrato}: sem consulta ao vivo: {type(e).__name__}: {e}")
        raise HTTPException(status_code=409, detail=f"sem acesso ao contrato {contrato} pela API da Hapvida")

