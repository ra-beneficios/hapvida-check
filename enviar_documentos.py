"""
Envio de documentos (Boleto, Nota Fiscal, Relatorio Analitico, Coparticipacao)
de um contrato do Grupo Saude por e-mail - generalizacao do que foi feito
manualmente pro EM593 (01/08). So Grupo Saude (login Carol, sem captcha);
Grupo Legado precisa de sessao manual, nao se aplica aqui.

Reaproveita censo_saude.py (login/sessao) e documentos_saude.py (checagem
de NFe/Relatorio) - nao reimplementa nada disso.

Envia via Microsoft Graph (client_credentials, app-only, mesma credencial
ja usada pelo n8n pra notificacoes - GCP secret "graph-noreply-app-only"),
como noreply@ra-beneficios.com.br.
"""
import base64
import io
import json
import mimetypes
import os
import re
import unicodedata
import zipfile
from datetime import date

import httpx
import psycopg2
import secrets_store
from playwright.sync_api import sync_playwright

from censo_saude import criar_sessao_empresa, login_saude
from documentos_saude import (
    baixar_boleto_pdf, checar_nota_fiscal, checar_relatorio_analitico, relatorio_coparticipacao, relatorio_coparticipacao_pdf,
)

SENDER = "noreply@ra-beneficios.com.br"

# Espelho local do SharePoint "Projeto_RA" (montado no docker-compose.yml
# a partir de /opt/projeto_ra/operadoras/hapvida/faturas - rclone bisync,
# ~10min pra aparecer na nuvem de verdade). Estrutura confirmada olhando o
# que o Cowork ja deixou la pro S7HNK (#152, 01/09/2026):
#   {NOME REAL DA EMPRESA}/{CODIGO}/{MESAno}/{TIPO}_{CODIGO}_{MESAno}.ext
# Pasta preserva o nome de verdade (Andre, 01/09/2026: "a ideia e que fique
# o nome real da Operadora") - so troca o que o SharePoint proibe em nome
# de pasta (principalmente "/", visto em "CONSORCIO POSSEBON/UPE/CPL" ->
# "CONSORCIO POSSEBON-UPE-CPL"), nao forca maiusculo nem underscore.
SHAREPOINT_FATURAS_BASE = "/sharepoint/faturas"

_MESES_ABREV_PT = {
    1: "JAN", 2: "FEV", 3: "MAR", 4: "ABR", 5: "MAI", 6: "JUN",
    7: "JUL", 8: "AGO", 9: "SET", 10: "OUT", 11: "NOV", 12: "DEZ",
}

_CARACTERES_ILEGAIS_PASTA = re.compile(r'[\\/:*?"<>|]')

# Tag de tipo por prefixo do nome que a gente mesmo da ao anexo (montar_e_
# enviar, acima) - precisa distinguir Saude/Coparticipacao pra nao colidir
# quando o contrato tem os dois tipos de fatura (o Cowork ainda nao bateu
# nesse caso nos exemplos vistos, so' tem 1 fatura por contrato Legado).
_TIPO_POR_PREFIXO = [
    ("Boleto_Saúde_", "BOLETO_SAUDE"),
    ("Boleto_Coparticipação_", "BOLETO_COPARTICIPACAO"),
    ("NotaFiscal_Saúde_", "NFE_SAUDE"),
    ("NotaFiscal_Coparticipação_", "NFE_COPARTICIPACAO"),
    # Padrao NOV26 (Andre, 05/10/2026): o plano entra no nome do relatorio.
    ("RelatorioAnalitico_FaturaMensal_", "RELATORIO_ANALITICO_SAUDE"),
    ("Coparticipacao_Analitico_", "RELATORIO_ANALITICO_COPARTICIPACAO"),
]


def _mes_ano_curto(data: date) -> str:
    return f"{_MESES_ABREV_PT[data.month]}{str(data.year)[2:]}"


def _nome_pasta_empresa(nome: str) -> str:
    return _CARACTERES_ILEGAIS_PASTA.sub("-", nome).strip()


def _tipo_documento(nome_arquivo: str) -> str:
    for prefixo, tipo in _TIPO_POR_PREFIXO:
        if nome_arquivo.startswith(prefixo):
            return tipo
    stem = os.path.splitext(nome_arquivo)[0]
    stem = unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", stem).strip("_").upper() or "ARQUIVO"


def salvar_sharepoint(empresa_nome: str, codigo: str, mes_ano_curto: str, anexos: list[tuple]) -> str | None:
    """Grava uma copia de cada anexo em
    /sharepoint/faturas/{EMPRESA}/{CODIGO}/{MESAno}/{TIPO}_{CODIGO}_{MESAno}.ext
    - convencao alinhada com o Cowork em 01/09/2026 (ver #152). Nunca deve
    derrubar o envio por causa disso - falha aqui e so' logada (retorna
    None), best-effort."""
    try:
        pasta = os.path.join(SHAREPOINT_FATURAS_BASE, _nome_pasta_empresa(empresa_nome), codigo, mes_ano_curto)
        os.makedirs(pasta, exist_ok=True)
        for nome_original, _, conteudo in anexos:
            ext = os.path.splitext(nome_original)[1]
            tipo = _tipo_documento(nome_original)
            with open(os.path.join(pasta, f"{tipo}_{codigo}_{mes_ano_curto}{ext}"), "wb") as f:
                f.write(conteudo)
        return pasta
    except Exception as e:
        print(f"[enviar_documentos] aviso: falha ao salvar no SharePoint ({empresa_nome}/{codigo}): {e}", flush=True)
        return None


def ler_pasta_sharepoint(empresa_nome: str, codigo: str, mes_ano_curto: str | None = None) -> list[tuple]:
    """Le os arquivos que ja foram deixados na pasta compartilhada pra um
    codigo/competencia - achado real 01/09/2026 (#152): pro Grupo Legado
    (Odonto Premium), o Cowork ja deixa os documentos la (boleto/NF/
    relatorio/lista de ativos) - NAO precisa mais pedir link de sessao
    manual pro Andre, so' ler o que ja esta na pasta. Generico de
    proposito (le TODOS os arquivos da pasta, sem exigir nome exato) -
    achado no mesmo dia: a convencao de nome do Cowork ainda varia por
    tipo de documento (ex: NFE as vezes leva o numero da nota em vez do
    MESAno, 'LISTA_ATIVOS' em vez de 'BENEFICIARIOS_ATIVOS') - exigir
    nome exato quebraria toda hora por causa disso.

    Se `mes_ano_curto` vier None, usa a subpasta de competencia mais
    RECENTEMENTE MODIFICADA (nao a maior em ordem alfabetica - 'OUT26'
    vem antes de 'SET26' no alfabeto mas e' depois no calendario).
    Devolve lista vazia se a pasta nao existir ainda (ninguem rodou esse
    codigo ainda) - nunca levanta excecao."""
    try:
        pasta_empresa = os.path.join(SHAREPOINT_FATURAS_BASE, _nome_pasta_empresa(empresa_nome), codigo)
        if not os.path.isdir(pasta_empresa):
            return []
        if mes_ano_curto is None:
            competencias = [d for d in os.listdir(pasta_empresa) if os.path.isdir(os.path.join(pasta_empresa, d))]
            if not competencias:
                return []
            mes_ano_curto = max(competencias, key=lambda d: os.path.getmtime(os.path.join(pasta_empresa, d)))
        pasta = os.path.join(pasta_empresa, mes_ano_curto)
        if not os.path.isdir(pasta):
            return []
        anexos = []
        for nome in sorted(os.listdir(pasta)):
            caminho = os.path.join(pasta, nome)
            if not os.path.isfile(caminho):
                continue
            ct = mimetypes.guess_type(nome)[0] or "application/octet-stream"
            with open(caminho, "rb") as f:
                anexos.append((nome, ct, f.read()))
        return anexos
    except Exception as e:
        print(f"[enviar_documentos] aviso: falha ao ler pasta compartilhada ({empresa_nome}/{codigo}): {e}", flush=True)
        return []


def checar_pares_boleto_nfe(nomes_arquivos: list[str]) -> list[str]:
    """Compara BOLETO com NFE (por qualificador SAUDE/COPARTICIPACAO,
    quando existir) - se um existe e o outro nao, e' sinal de download
    parcial (nao falta real) - achado 01/09/2026 (#152): o YS55U foi
    enviado com boleto mas sem NF sem nenhum aviso, porque o fluxo que le
    pasta compartilhada (ler_pasta_sharepoint) nao tinha esse tipo de
    checagem (diferente do fluxo ao vivo, que agora avisa via `avisos`).
    SEMPRE chamar isso ANTES de enviar quando os anexos vierem de pasta
    compartilhada, e resolver o que aparecer aqui antes de mandar - pra
    nao ter que reenviar depois e duplicar o e-mail no cliente."""

    def normaliza(s: str) -> str:
        return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().upper()

    def qualificador(nome: str) -> str:
        n = normaliza(nome)
        if "COPARTICIPACAO" in n:
            return "COPARTICIPACAO"
        if "SAUDE" in n or "MENSALIDADE" in n:
            return "SAUDE"
        return ""

    boletos = {qualificador(n) for n in nomes_arquivos if "BOLETO" in normaliza(n)}
    nfes = {qualificador(n) for n in nomes_arquivos if "NFE" in normaliza(n) or "NOTAFISCAL" in normaliza(n)}
    avisos = []
    for q in sorted(boletos - nfes):
        avisos.append(f"Boleto {q or '(único)'} presente mas NF correspondente NÃO encontrada — possível download parcial.")
    for q in sorted(nfes - boletos):
        avisos.append(f"NF {q or '(único)'} presente mas Boleto correspondente NÃO encontrado — possível download parcial.")
    return avisos


# Limite pratico de anexos "soltos" (base64 inline) numa unica chamada
# sendMail do Graph - acima disso zipamos tudo num unico arquivo (#58).
LIMITE_ANEXOS_BYTES = 3 * 1024 * 1024


def _get_graph_credenciais(project: str) -> dict:
    return secrets_store.get_json("graph-noreply-app-only", project)


def _get_graph_token(creds: dict) -> str:
    url = f"https://login.microsoftonline.com/{creds['tenant_id']}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "scope": "https://graph.microsoft.com/.default",
    }
    r = httpx.post(url, data=data, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def _baixar_nfe_pdf(invoice_path: str) -> bytes | None:
    """A NFe do Grupo Saude e uma pagina HTML do portal municipal do prestador
    (ex: nfe.prefeitura.sp.gov.br) - mas essa pagina externa e so uma
    MOLDURA (botoes Download/Imprimir/Fechar), o PDF de verdade fica num
    <iframe src="..."> dentro dela. Renderizar a pagina externa via
    page.pdf() (como era antes) captura so a moldura vazia - bug real,
    achado em 31/08/2026 com as 2 NFs do KGBQY chegando em branco no
    e-mail. Fix: acha a URL do iframe e baixa o PDF direto de la."""
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.goto(invoice_path, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)
            iframe = page.query_selector("iframe")
            pdf_url = iframe.get_attribute("src") if iframe else None
            if pdf_url and not pdf_url.startswith("http"):
                from urllib.parse import urljoin

                pdf_url = urljoin(page.url, pdf_url)
            browser.close()
        if not pdf_url:
            return None
        r = httpx.get(pdf_url, timeout=30)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/pdf"):
            return r.content
        return None
    except Exception:
        return None


def _buscar_faturas_contrato(codigo: str) -> list[dict]:
    """Fatura de saude (mais recente) + fatura de coparticipacao (mais
    recente), se existir - mesmo criterio usado no e-mail modelo do EM593."""
    conn = psycopg2.connect(dbname="lifetools")
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (bill_type) billing_number, numero, bill_type, vencimento, valor,
                       person_number, remittence
                FROM faturas f JOIN empresa_operadora_codigos eoc ON eoc.id = f.empresa_operadora_codigo_id
                WHERE eoc.codigo_externo = %s
                ORDER BY bill_type, vencimento DESC
                """,
                (codigo,),
            )
            cols = ["billing_number", "numero", "bill_type", "vencimento", "valor", "person_number", "remittence"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _empresa_nome(codigo: str) -> str:
    conn = psycopg2.connect(dbname="lifetools")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT e.nome FROM empresa_operadora_codigos eoc JOIN empresas e ON e.id = eoc.empresa_id "
                "WHERE eoc.codigo_externo = %s",
                (codigo,),
            )
            row = cur.fetchone()
            return row[0] if row else codigo
    finally:
        conn.close()


def _localidade(codigo: str) -> str | None:
    """Coluna `unidade` de empresa_operadora_codigos - existia sem uso,
    reaproveitada 01/09/2026 (#152) pra guardar localidade/filial quando a
    mesma empresa (mesmo CNPJ) tem varios codigos em cidades diferentes
    (ex: LH Engenharia - ERQ43/VKTX1 Brasilia, ICBJY/GRKKH Sergipe). So'
    preenchida quando o Andre confirma - nao inventar/deduzir localidade."""
    conn = psycopg2.connect(dbname="lifetools")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT unidade FROM empresa_operadora_codigos WHERE codigo_externo = %s", (codigo,))
            row = cur.fetchone()
            return row[0] if row and row[0] else None
    finally:
        conn.close()


def montar_assunto(empresa_nome: str, vencimento: str, localidade: str | None = None) -> str:
    """Assunto padrao do e-mail (Andre, 01/09/2026): 'Envio de faturamento
    – {empresa}[ - {localidade}] | Vencimento {data}'. Localidade so' entra
    quando tem (ver _localidade) - nunca inventar."""
    nome = f"{empresa_nome} - {localidade}" if localidade else empresa_nome
    return f"Envio de faturamento – {nome} | Vencimento {vencimento}"


_MESES_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril", 5: "Maio", 6: "Junho",
    7: "Julho", 8: "Agosto", 9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}


def _mes_referencia(data: date) -> str:
    return f"{_MESES_PT[data.month]}/{data.year}"


def montar_corpo_html(
    empresa_nome: str,
    contratos_desc: str,
    mes_referencia: str,
    vencimento: str,
    tabela: str,
    zip_aviso: str = "",
    aviso_html: str = "",
) -> str:
    """Corpo padrao do e-mail de documentos de faturamento (#152/#58) -
    modelo definido pelo Andre em 31/08/2026 (ver 152_processo_envio_
    documentos_faturamento.md). `contratos_desc` e o trecho depois do nome
    da empresa, ex: '(contrato KGBQY)' ou 'Saúde (contrato 8DAC9) e Odonto
    Premium (contrato S7HNK)'.

    REGRA FIXA (Andre, 01/09/2026 - reforçando a de 31/08): `aviso_html`
    NUNCA leva informacao operacional/tecnica pro cliente - nem
    inadimplencia (tabela vermelha "Vencido"), nem "documento X nao pode
    ser baixado"/"falha no portal". Achado real: WP64K e 2XP6W saíram pro
    cliente com texto tecnico de NF que falhou no download - inadmissivel,
    corrigido na hora (`montar_e_enviar` parou de passar `avisos` pra ca).
    Esse parametro so' existe pra quando o time de Pos-Vendas EXPLICITAMENTE
    decidir que um aviso deve aparecer pro cliente (raro, e' decisao de
    tom/conteudo do time, nao automatica) - nunca popular sozinho a partir
    de `avisos` de falha tecnica. Informacao tecnica de falha fica SO no
    retorno da funcao pra quem esta operando decidir o que fazer."""
    return f"""
    <div style="font-family:sans-serif;color:#222">
      <p>Olá,</p>
      <p>Já estão disponíveis os documentos referente ao faturamento de <b>{mes_referencia}</b>,
      com vencimento em <b>{vencimento}</b>. Junto, seguem outros anexos que completam a base de
      faturamento deste contrato. Qualquer dúvida, entre em contato com seu executivo de Relacionamento.</p>
      <p>Seguem todos os documentos referentes ao <b>{empresa_nome}</b> — {contratos_desc}:</p>
      {zip_aviso}
      {tabela}
      {aviso_html}
      <p style="color:#666;font-size:12px">Enviado por Cora - Assistente Digital - RA Benefícios</p>
    </div>
    """


def montar_tabela_html(anexos_meta: list[dict]) -> str:
    linhas = "".join(
        f"""
        <tr>
          <td style="padding:6px 12px;border-bottom:1px solid #e0e0e0;font-family:sans-serif;font-size:13px">{a['nome']}</td>
          <td style="padding:6px 12px;border-bottom:1px solid #e0e0e0;font-family:sans-serif;font-size:13px;color:#444">{a['descricao']}</td>
        </tr>"""
        for a in anexos_meta
    )
    return f"""
    <table style="border-collapse:collapse;width:100%;max-width:640px;margin:12px 0">
      <thead>
        <tr>
          <th style="text-align:left;padding:6px 12px;border-bottom:2px solid #333;font-family:sans-serif;font-size:13px">Anexo</th>
          <th style="text-align:left;padding:6px 12px;border-bottom:2px solid #333;font-family:sans-serif;font-size:13px">Descrição</th>
        </tr>
      </thead>
      <tbody>{linhas}
      </tbody>
    </table>
    """


def montar_e_enviar(
    codigo: str, destinatario: str, project: str, usuario: str, senha: str,
    enviar_email: bool = True, cc: str = "", bcc: str = "",
) -> dict:
    empresa_nome = _empresa_nome(codigo)
    faturas = _buscar_faturas_contrato(codigo)
    if not faturas:
        return {"status": "erro", "detalhe": f"Nenhuma fatura encontrada pra {codigo} — nada pra enviar."}
    # Regra NOV26 (Andre, 05/10/2026): so' o mes da competencia. A busca pega a
    # mais recente de cada tipo; copart de mes anterior (contrato sem copart no
    # mes) iria parar na pasta do mes da mensalidade. Fica so' o que vence no
    # mesmo mes da mensalidade.
    ref = next((f for f in faturas if f["bill_type"] == "MENSALIDADE PLANO SAUDE"), faturas[0])["vencimento"]
    faturas = [f for f in faturas if (f["vencimento"].year, f["vencimento"].month) == (ref.year, ref.month)]

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        try:
            auth = login_saude(page, usuario, senha)
        finally:
            browser.close()

    anexos = []
    anexos_meta = []
    avisos = []

    for fatura in faturas:
        billing_number = fatura["billing_number"]
        tipo_label = "Saúde" if fatura["bill_type"] == "MENSALIDADE PLANO SAUDE" else "Coparticipação"

        try:
            boleto_bytes = baixar_boleto_pdf(auth, codigo, billing_number)
            nome = f"Boleto_{tipo_label}_{codigo}.pdf"
            anexos.append((nome, "application/pdf", boleto_bytes))
            anexos_meta.append({"nome": nome, "descricao": f"Boleto de cobrança — {tipo_label.lower()} (venc. {fatura['vencimento']}, R$ {fatura['valor']})."})
        except Exception as e:
            avisos.append(f"Boleto {tipo_label} não pôde ser baixado: {e}")

        nfe = checar_nota_fiscal(auth, codigo, billing_number)
        if nfe and nfe.get("invoicePath"):
            pdf_bytes = _baixar_nfe_pdf(nfe["invoicePath"])
            if pdf_bytes:
                nome = f"NotaFiscal_{tipo_label}_{codigo}.pdf"
                anexos.append((nome, "application/pdf", pdf_bytes))
                anexos_meta.append({"nome": nome, "descricao": f"Nota fiscal eletrônica de serviços — {tipo_label.lower()} (NF-e nº {nfe.get('invoiceNumber')})."})
            else:
                avisos.append(
                    f"NF {tipo_label} encontrada no portal mas o PDF não pôde ser baixado — "
                    "possível falha transitória, considerar reenviar antes de mandar pro cliente."
                )
        else:
            avisos.append(
                f"NF {tipo_label} não retornada pelo portal nesta consulta (billing_number {billing_number}) — "
                "pode ser falha transitória ou realmente não emitida ainda; conferir antes de reenviar pra não duplicar."
            )

        if fatura["bill_type"] == "MENSALIDADE PLANO SAUDE":
            for tipo, ext, ct in [("PDF", "pdf", "application/pdf"), ("CSV", "csv", "text/csv")]:
                rel = checar_relatorio_analitico(auth, codigo, billing_number, tipo)
                if rel and rel.get("link"):
                    r = httpx.get(rel["link"], timeout=60)
                    if r.status_code == 200:
                        nome = f"RelatorioAnalitico_FaturaMensal_{codigo}.{ext}"
                        anexos.append((nome, ct, r.content))
                        anexos_meta.append({"nome": nome, "descricao": f"Relatório analítico da fatura mensal (saúde), formato {tipo}."})
                    else:
                        avisos.append(f"Relatório analítico ({tipo}) encontrado mas download falhou (HTTP {r.status_code}).")
                else:
                    avisos.append(f"Relatório analítico ({tipo}) não retornado pelo portal nesta consulta (billing_number {billing_number}).")

        # Andre, 01/10/2026: o analitico de coparticipacao e' o PDF oficial do
        # portal (antes era um CSV montado do nosso banco), e a lista de
        # ativos e' so' a oficial (LISTA_ATIVOS, do faturamento_mensal) - a
        # BENEFICIARIOS_ATIVOS do banco saiu.
        if fatura["bill_type"] != "MENSALIDADE PLANO SAUDE":
            if fatura.get("person_number") and fatura.get("remittence"):
                # PDF vai pro cliente; CSV (mesma fatura) e' fonte do XLSX e da
                # conferencia total x boleto (regra NOV26, Andre 05/10/2026).
                for formato, ct in (("PDF", "application/pdf"), ("CSV", "text/csv")):
                    conteudo, aviso = relatorio_coparticipacao(
                        auth, codigo, billing_number, fatura["person_number"], fatura["remittence"], formato
                    )
                    if conteudo:
                        nome = f"Coparticipacao_Analitico_{codigo}.{formato.lower()}"
                        anexos.append((nome, ct, conteudo))
                        anexos_meta.append({"nome": nome, "descricao": "Relatório analítico da coparticipação do mês, por beneficiário e procedimento."})
                    if aviso:
                        avisos.append(aviso)
            else:
                avisos.append(f"Relatório de coparticipação: fatura {billing_number} sem person_number/remittence no banco.")

    if not anexos:
        return {"status": "erro", "detalhe": "Nenhum documento disponível pra anexar — nada foi enviado.", "avisos": avisos}

    fatura_saude = next((f for f in faturas if f["bill_type"] == "MENSALIDADE PLANO SAUDE"), faturas[0])
    mes_ano_curto = _mes_ano_curto(fatura_saude["vencimento"])
    pasta_sharepoint = salvar_sharepoint(empresa_nome, codigo, mes_ano_curto, anexos)

    if not enviar_email:
        # Modo so'-salvar (#152, 01/09/2026): busca e grava no SharePoint,
        # NAO manda e-mail nenhum - usado pro lote de "todas as empresas"
        # sem gerar e-mail de teste a toa pra cada codigo.
        return {
            "status": "ok",
            "enviado": False,
            "anexos": [nome for nome, _, _ in anexos],
            "avisos": avisos,
            "pasta_sharepoint": pasta_sharepoint,
        }

    graph_creds = _get_graph_credenciais(project)
    token = _get_graph_token(graph_creds)

    # Regra NOV26 (Andre, 05/10/2026): CSV nunca vai pro cliente - fica so' na
    # pasta, como fonte do XLSX e da conferencia de valor.
    anexos = [a for a in anexos if not a[0].lower().endswith(".csv")]
    anexos_meta = [m for m in anexos_meta if not m["nome"].lower().endswith(".csv")]
    tamanho_total = sum(len(conteudo) for _, _, conteudo in anexos)
    zipado = tamanho_total > LIMITE_ANEXOS_BYTES
    if zipado:
        buf_zip = io.BytesIO()
        with zipfile.ZipFile(buf_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for nome, _, conteudo in anexos:
                zf.writestr(nome, conteudo)
        nome_zip = f"Documentos_{codigo}_{date.today().strftime('%m-%Y')}.zip"
        anexos_envio = [(nome_zip, "application/zip", buf_zip.getvalue())]
    else:
        anexos_envio = anexos

    tabela = montar_tabela_html(anexos_meta)
    # REGRA FIXA (Andre, 01/09/2026): `avisos` (documento que faltou/falhou
    # baixar) e' informacao OPERACIONAL - NUNCA vai pro corpo do e-mail que
    # o cliente ve. So aparece no retorno da funcao (pra quem esta operando
    # decidir se reenvia antes de mandar). Achado depois de 2 envios reais
    # (WP64K, 2XP6W) saírem com esse texto tecnico visivel pro cliente -
    # inadmissivel, corrigido na hora. Ver tambem a regra irma sobre
    # inadimplencia em montar_corpo_html - mesmo principio.
    zip_aviso = (
        f"<p style='color:#666;font-size:12px'>Os {len(anexos)} arquivos abaixo somam "
        f"{tamanho_total / 1024 / 1024:.1f}MB e foram compactados num único arquivo "
        f"(<b>{nome_zip}</b>) por conta do limite de tamanho de anexo do e-mail.</p>"
        if zipado
        else ""
    )
    corpo_html = montar_corpo_html(
        empresa_nome=empresa_nome,
        contratos_desc=f"(contrato {codigo})",
        mes_referencia=_mes_referencia(fatura_saude["vencimento"]),
        vencimento=fatura_saude["vencimento"].strftime("%d/%m/%Y"),
        tabela=tabela,
        zip_aviso=zip_aviso,
    )

    graph_anexos = [
        {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": nome,
            "contentType": ct,
            "contentBytes": base64.b64encode(conteudo).decode(),
        }
        for nome, ct, conteudo in anexos_envio
    ]

    destinatarios = [e.strip() for e in destinatario.split(",") if e.strip()]
    cc_lista = [e.strip() for e in cc.split(",") if e.strip()]
    bcc_lista = [e.strip() for e in bcc.split(",") if e.strip()]

    mensagem = {
        "message": {
            "subject": montar_assunto(empresa_nome, fatura_saude["vencimento"].strftime("%d/%m/%Y"), _localidade(codigo)),
            "body": {"contentType": "HTML", "content": corpo_html},
            "toRecipients": [{"emailAddress": {"address": e}} for e in destinatarios],
            "ccRecipients": [{"emailAddress": {"address": e}} for e in cc_lista],
            "bccRecipients": [{"emailAddress": {"address": e}} for e in bcc_lista],
            "attachments": graph_anexos,
        },
        "saveToSentItems": True,
    }

    url = f"https://graph.microsoft.com/v1.0/users/{SENDER}/sendMail"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = httpx.post(url, headers=headers, content=json.dumps(mensagem), timeout=60)

    return {
        "status": "ok" if r.status_code < 300 else "erro",
        "http_status": r.status_code,
        "http_body": r.text[:500] if r.status_code >= 300 else None,
        "destinatarios": destinatarios,
        "cc": cc_lista,
        "bcc": bcc_lista,
        "anexos": [nome for nome, _, _ in anexos],
        "zipado": zipado,
        "tamanho_total_mb": round(tamanho_total / 1024 / 1024, 2),
        "avisos": avisos,
        "pasta_sharepoint": pasta_sharepoint,
    }
