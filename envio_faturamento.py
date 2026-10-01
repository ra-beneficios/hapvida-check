"""
Motor de envio mensal de faturamento Hapvida (Andre, 30/09/2026) - generaliza
o primeiro lote real (OUT26, 26 clientes, envio_lote_out26.py).

Le SO' a pasta da competencia (faturas/{EMPRESA}/{CODIGO}/{MESAno}/), monta 1
e-mail por empresa+localidade (Saude + Odonto Premium SEMPRE juntos) e envia
via Graph como noreply@. NUNCA envia grupo com arquivo faltando - bloqueia e
lista no relatorio. Log anti-duplicidade em _status/ENVIOS_{COMP}.json: grupo
ja enviado nunca e' reenviado.

Agenda (cron no host; procedimento completo em operadoras/hapvida/PROCEDIMENTO_MENSAL.md):
  dia 27 08:00  1o envio (tudo o que estiver completo)
  dia 01 08:00  2o envio (o que completou depois)
  dia 05 08:00  3o envio (--final: relatorio sai mesmo sem envio novo)
Competencia = mes do vencimento (mesma regra do faturamento_mensal: do dia 20
em diante e' o mes seguinte).

Pausar tudo: criar o arquivo _status/ENVIO_PAUSADO (qualquer conteudo).

Uso: python3 envio_faturamento.py [--enviar] [--final] [--competencia=NOV26]
Sem --enviar e' so' previa (nada sai).
"""
import base64
import csv
import html
import io
import json
import os
import re
import shutil
import sys
import time
import zipfile
from datetime import date, datetime

import httpx
import openpyxl
import psycopg2

import enviar_documentos as ed
import faturamento_mensal as fm

BASE = ed.SHAREPOINT_FATURAS_BASE
STATUS_DIR = os.path.join(BASE, "_status")
RELATORIO_PARA = ["C-level@ra-beneficios.com.br", "financeiro@ra-beneficios.com.br", "relacionamento@ra-beneficios.com.br"]
# Boaventura: combinado com o Andre, contrato so' de vidas, sem faturamento (#150).
EXCLUIR = {"9WLZU", "9GBKA"}
MODALIDADE = {"SAUDE": "Saúde", "ODONTO_PREMIUM": "Odonto Premium"}
LIMITE_ENVIO_BYTES = int(3.3 * 1024 * 1024)  # zip de 3,06MB passou em 30/09; acima disso bloqueia
LISTA_ATIVOS_IDADE_MAX_DIAS = 10

DESCR = [
    ("BOLETO_COPARTICIPACAO", "Boleto de cobrança — coparticipação"),
    ("BOLETO_SAUDE", "Boleto de cobrança — mensalidade saúde"),
    ("BOLETO", "Boleto de cobrança"),
    ("NFE_COPARTICIPACAO", "Nota fiscal eletrônica de serviços — coparticipação"),
    ("NFE_SAUDE", "Nota fiscal eletrônica de serviços — mensalidade saúde"),
    ("NFE", "Nota fiscal eletrônica de serviços"),
    ("RELATORIO_ANALITICO", "Relatório analítico da fatura mensal"),
    ("COPARTICIPACAO_ANALITICO", "Detalhe por beneficiário e procedimento dos valores de coparticipação do período"),
    ("LISTA_ATIVOS", "Lista de beneficiários ativos do contrato"),
]
# Andre, 01/10/2026: lista de ativos que vai pro cliente e' so' a oficial da
# operadora (LISTA_ATIVOS). BENEFICIARIOS_ATIVOS (gerada do nosso banco) nao
# e' mais baixada; arquivo antigo que ainda esteja na pasta e' ignorado.
IGNORAR = ("BENEFICIARIOS_ATIVOS",)
ANEXAVEIS = (".pdf", ".xlsx")


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def descricao(nome, modal):
    for p, d in DESCR:
        if nome.upper().startswith(p):
            return f"{d} ({modal}, formato {nome.rsplit('.', 1)[-1].upper()})."
    return None


def status_legado_linhas(comp):
    """Linhas do CSV de status do Cowork com vencimento na competencia e numero emitido."""
    mes, ano = fm.competencia_para_mes(comp)
    arqs = sorted((os.path.join(STATUS_DIR, n) for n in os.listdir(STATUS_DIR)
                   if n.startswith("STATUS_FATURAS_") and n.endswith(".csv")), key=os.path.getmtime)
    res = {}
    if not arqs:
        return res
    with open(arqs[-1], encoding="utf-8-sig") as f:
        for l in csv.DictReader(f, delimiter=";"):
            try:
                d, m, a = l["Vencimento"].split("/")
                venc = date(2000 + int(a[-2:]), int(m), int(d))
            except (ValueError, KeyError, AttributeError):
                continue
            if (venc.month, venc.year) != (mes, ano) or not (l.get("NumeroDocumento") or "").strip():
                continue
            r = res.setdefault(l["Codigo"].strip(), {"BOLETO": set(), "NFE": set(), "venc": venc})
            tipo = (l.get("TipoDocumento") or "").strip().upper()
            if tipo in r:
                r[tipo].add(l["NumeroDocumento"].strip())
            r["venc"] = min(r["venc"], venc)
    return res


PLANILHA_CONTATOS = "/projeto_ra/operadoras/Access/Hapvida/empresas_contato_email.xlsx"
_RE_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")


def sincronizar_contatos(conn):
    """Planilha de e-mails do faturamento (mantida pelo time no SharePoint) ->
    contatos_faturamento, antes de cada envio (Andre, 01/10/2026; ate' aqui era
    manual). Le' todas as colunas da E em diante (UTC tinha e-mail em K/L sem
    cabecalho). Contrato que ficar sem nenhum e-mail na planilha mantem o que
    esta' no banco - nunca apaga lista por planilha mal preenchida."""
    if not os.path.exists(PLANILHA_CONTATOS):
        log(f"contatos: planilha não encontrada ({PLANILHA_CONTATOS}) - banco mantido")
        return []
    ws = openpyxl.load_workbook(PLANILHA_CONTATOS, read_only=True, data_only=True).worksheets[0]
    with conn.cursor() as cur:
        cur.execute("SELECT codigo_externo, emails FROM contatos_faturamento")
        banco = {c: set(e or []) for c, e in cur.fetchall()}
    mudancas = []
    with conn.cursor() as cur:
        for r in list(ws.iter_rows(values_only=True))[1:]:
            if not r or len(r) < 5 or not r[1]:
                continue
            codigo, emails = str(r[1]).strip(), []
            for v in r[4:]:
                for e in _RE_EMAIL.findall(str(v or "")):
                    if e.lower() not in emails:
                        emails.append(e.lower())
            if not emails or set(emails) == banco.get(codigo):
                continue
            antes = banco.get(codigo, set())
            cur.execute(
                """INSERT INTO contatos_faturamento (codigo_externo, empresa_nome, responsavel_ra, emails, fonte, atualizado_em)
                   VALUES (%s, %s, %s, %s, %s, now())
                   ON CONFLICT (codigo_externo) DO UPDATE SET emails = EXCLUDED.emails, fonte = EXCLUDED.fonte, atualizado_em = now()""",
                (codigo, r[2], r[0], emails, f"empresas_contato_email.xlsx ({date.today():%d/%m})"))
            mudancas.append(f"{codigo}: +{sorted(set(emails) - antes)} -{sorted(antes - set(emails))}")
    conn.commit()
    for m in mudancas:
        log(f"contatos: {m}")
    log(f"contatos: {len(mudancas)} contrato(s) atualizados da planilha")
    return mudancas


def montar_grupos(conn, comp):
    saude = fm.contratos_saude(comp)            # codigo -> {SAUDE, COPARTICIPACAO}
    legado = fm.contratos_legado()              # codigo -> empresa (Status=Ativo)
    escopo = (set(saude) | set(legado)) - EXCLUIR
    with conn.cursor() as cur:
        cur.execute("""
            SELECT eoc.codigo_externo, eoc.modalidade, coalesce(eoc.unidade, ''), e.id, e.nome
            FROM empresa_operadora_codigos eoc JOIN empresas e ON e.id = eoc.empresa_id
            WHERE eoc.operadora_id = 1 AND eoc.codigo_externo = ANY(%s)
              AND eoc.modalidade IN ('SAUDE', 'ODONTO_PREMIUM')""", (list(escopo),))
        rows = cur.fetchall()
    grupos = {}
    for cod, modal, unidade, emp_id, emp_nome in rows:
        g = grupos.setdefault((emp_id, unidade), {"cliente": emp_nome, "unidade": unidade or None, "codigos": []})
        g["codigos"].append({"codigo": cod, "modalidade": modal, "portal": "saude" if cod in saude else "legado",
                             "tipos": saude.get(cod, set())})
    # So' faz grupo se tiver cobranca na competencia: Saude via banco, Legado via status do Cowork ou pasta.
    st = status_legado_linhas(comp)
    finais = []
    for g in grupos.values():
        g["codigos"].sort(key=lambda c: (c["modalidade"] != "SAUDE", c["codigo"]))
        ativos = [c for c in g["codigos"] if c["portal"] == "saude" or c["codigo"] in st
                  or fm.arquivos(fm.pasta_contrato(c["codigo"], comp))]
        if ativos:
            g["codigos"] = ativos
            g["chave"] = "+".join(c["codigo"] for c in ativos)
            finais.append(g)
    return sorted(finais, key=lambda g: g["cliente"]), st


def lista_ativos_fallback(cod, comp):
    """Legado: o job semanal do Cowork pode ter gravado a lista na pasta do mes
    anterior (feito a mao em 30/09 pro OUT26). Copia se for recente."""
    ant = fm.pasta_contrato(cod, fm.competencia_anterior(comp))
    for n in fm.arquivos(ant):
        if n.upper().startswith("LISTA_ATIVOS") and n.lower().endswith(".csv"):
            orig = os.path.join(ant, n)
            if (time.time() - os.path.getmtime(orig)) / 86400 <= LISTA_ATIVOS_IDADE_MAX_DIAS:
                destino_dir = fm.pasta_contrato(cod, comp)
                os.makedirs(destino_dir, exist_ok=True)
                shutil.copy2(orig, os.path.join(destino_dir, f"LISTA_ATIVOS_{cod}_{comp}.csv"))
                return True
    return False


def checar_grupo(conn, g, comp, st):
    anexos, meta, faltas, venc = [], [], [], None
    for c in g["codigos"]:
        cod, modal = c["codigo"], MODALIDADE[c["modalidade"]]
        pasta = fm.pasta_contrato(cod, comp)
        nomes = [n for n in fm.arquivos(pasta) if not n.upper().startswith(IGNORAR)]
        if c["portal"] == "legado" and nomes and not any(n.upper().startswith("LISTA_ATIVOS") for n in nomes):
            if lista_ativos_fallback(cod, comp):
                log(f"{cod}: lista de ativos copiada do mês anterior")
                fm.gerar_xlsx_pasta(pasta)
                nomes = [n for n in fm.arquivos(pasta) if not n.upper().startswith(IGNORAR)]
        up = [n.upper() for n in nomes]
        tem = lambda p: any(n.startswith(p) for n in up)
        if not nomes:
            faltas.append(f"{cod}: pasta {comp} vazia")
            continue
        if not tem("LISTA_ATIVOS"):
            faltas.append(f"{cod}: lista de ativos")
        for ext in (".PDF", ".XLSX"):
            if not any(n.startswith("RELATORIO_ANALITICO") and n.endswith(ext) for n in up):
                faltas.append(f"{cod}: relatório analítico {ext[1:]}")
        if c["portal"] == "saude":
            for t in sorted(c["tipos"]):
                for p in (f"BOLETO_{t}", f"NFE_{t}"):
                    if not tem(p):
                        faltas.append(f"{cod}: {p}")
            if "COPARTICIPACAO" in c["tipos"] and not any(
                    n.startswith("COPARTICIPACAO_ANALITICO") and n.endswith(".PDF") for n in up):
                faltas.append(f"{cod}: analítico coparticipação PDF")
            with conn.cursor() as cur:
                mes, ano = fm.competencia_para_mes(comp)
                cur.execute("""SELECT min(f.vencimento) FROM faturas f JOIN empresa_operadora_codigos e
                               ON e.id = f.empresa_operadora_codigo_id WHERE e.codigo_externo = %s
                               AND f.bill_type = 'MENSALIDADE PLANO SAUDE'
                               AND extract(month FROM f.vencimento) = %s AND extract(year FROM f.vencimento) = %s""",
                            (cod, mes, ano))
                v = cur.fetchone()[0]
            venc = min(venc, v) if venc and v else (venc or v)
        else:
            # Legado: NF vem com o numero da nota no nome (sem dizer se e' mensalidade/copart)
            # -> confere por quantidade contra o status do Cowork.
            n_bol = sum(n.startswith("BOLETO") for n in up)
            n_nfe = sum(n.startswith("NFE") for n in up)
            s = st.get(cod)
            esperado_bol = len(s["BOLETO"]) if s else 1
            esperado_nfe = max(len(s["NFE"]) if s else 0, esperado_bol)
            if n_bol < esperado_bol:
                faltas.append(f"{cod}: boleto ({n_bol}/{esperado_bol})")
            if n_nfe < esperado_nfe:
                faltas.append(f"{cod}: NF ({n_nfe}/{esperado_nfe})")
            if s and c["modalidade"] == "SAUDE" or (s and venc is None):
                venc = min(venc, s["venc"]) if venc else s["venc"]
        for n in nomes:
            # Andre, 01/10/2026: cliente recebe so' PDF e XLSX - o CSV fica na
            # pasta como fonte do XLSX; sobra do sync (.sharepoint) tambem fica.
            if not n.lower().endswith(ANEXAVEIS):
                continue
            # copart do banco (CSV/XLSX, ate' OUT26) nao vai mais: so' o PDF oficial.
            if n.upper().startswith("COPARTICIPACAO_ANALITICO") and not n.lower().endswith(".pdf"):
                continue
            d = descricao(n, modal)
            if d is None:
                faltas.append(f"{cod}: arquivo inesperado {n}")
                continue
            with open(os.path.join(pasta, n), "rb") as f:
                anexos.append((n, ed.mimetypes.guess_type(n)[0] or "application/octet-stream", f.read()))
            meta.append({"nome": n, "descricao": d})
    emails = []
    with conn.cursor() as cur:
        for c in g["codigos"]:
            cur.execute("SELECT emails FROM contatos_faturamento WHERE codigo_externo = %s", (c["codigo"],))
            for (es,) in cur.fetchall():
                for e in es or []:
                    if e.lower() not in emails:
                        emails.append(e.lower())
    if not emails:
        faltas.append("sem e-mail cadastrado")
    if venc is None:
        faltas.append("vencimento não encontrado")
    return anexos, meta, faltas, emails, venc


def preparar_anexos(anexos, chave, comp):
    total = sum(len(c) for _, _, c in anexos)
    if total <= ed.LIMITE_ANEXOS_BYTES:
        return anexos, "", None
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, _, c in anexos:
            zf.writestr(n, c)
    nz = f"Documentos_{chave.replace('+', '_')}_{comp}.zip"
    aviso = (f"<p style='color:#666;font-size:12px'>Os {len(anexos)} arquivos abaixo somam {total/1024/1024:.1f}MB "
             f"e foram compactados num único arquivo (<b>{nz}</b>) por conta do limite de tamanho de anexo do e-mail.</p>")
    return [(nz, "application/zip", buf.getvalue())], aviso, nz


def graph_send(token, msg):
    return httpx.post(f"https://graph.microsoft.com/v1.0/users/{ed.SENDER}/sendMail",
                      headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                      content=json.dumps(msg), timeout=180)


def enviar_relatorio(token, comp, enviados_rodada, bloqueados, venc_ref):
    mes_ano = ed._mes_referencia(venc_ref) if venc_ref else comp
    titulo = f"Relatório de Envios — Faturamento {mes_ano} ({len(enviados_rodada)} clientes)"
    td = 'style="padding:6px 10px;border:1px solid #d0d0d0;font-family:sans-serif;font-size:12px;vertical-align:top"'
    th = ('style="padding:6px 10px;border:1px solid #d0d0d0;background:#1f3b5c;color:#fff;'
          'font-family:sans-serif;font-size:12px;text-align:left"')
    linhas = ""
    for r in enviados_rodada:
        arqs = "<br>".join(html.escape(a) for a in r["arquivos"])
        if r.get("zip"):
            arqs = f"<i>(compactados em {html.escape(r['zip'])})</i><br>" + arqs
        linhas += (f"<tr><td {td}>{html.escape(r['cliente'])}</td><td {td}>{r['codigos'].replace('+', '<br>')}</td>"
                   f"<td {td}>{r['data_envio']}</td><td {td}>{len(r['arquivos'])} arquivos:<br>{arqs}</td>"
                   f"<td {td}>{'<br>'.join(html.escape(e) for e in r['emails'])}</td></tr>")
    tabela = (f"<table style='border-collapse:collapse;width:100%'><thead><tr><th {th}>Cliente</th><th {th}>Código</th>"
              f"<th {th}>Data de Envio</th><th {th}>Arquivos Enviados</th><th {th}>E-mails enviados</th></tr></thead>"
              f"<tbody>{linhas}</tbody></table>") if linhas else "<p>Nenhum envio novo nesta rodada.</p>"
    pend = "".join(f"<li><b>{html.escape(b['cliente'])}</b> ({b['codigos']}): {html.escape('; '.join(b['faltas']))}</li>"
                   for b in bloqueados)
    pend_html = f"<p><b>Não enviados (arquivo faltando ou sem cadastro):</b></p><ul>{pend}</ul>" if pend else \
        "<p>Nenhum cliente pendente.</p>"
    corpo = (f"<div style='font-family:sans-serif;color:#222'><h2 style='font-size:18px'>{titulo}</h2>"
             f"<p>Rodada de {datetime.now():%d/%m/%Y %H:%M} — competência {comp}. Cada e-mail só é enviado com todos os "
             f"documentos completos.</p>{tabela}{pend_html}"
             f"<p style='color:#666;font-size:12px'>Enviado por Cora - Assistente Digital - RA Benefícios</p></div>")
    msg = {"message": {"subject": titulo, "body": {"contentType": "HTML", "content": corpo},
                       "toRecipients": [{"emailAddress": {"address": e}} for e in RELATORIO_PARA]},
           "saveToSentItems": True}
    r = graph_send(token, msg)
    log(f"relatório: HTTP {r.status_code} | {titulo}")


def escrever_resumo(comp, log_envios, bloqueados):
    md = [f"# Envios de faturamento — {comp}", "", f"Atualizado em {datetime.now():%d/%m/%Y %H:%M}.", "",
          "## Enviados", "", "| Cliente | Códigos | Data | Arquivos | E-mails |", "|---|---|---|---|---|"]
    md += [f"| {r['cliente']} | {r['codigos']} | {r['data_envio']} | {len(r['arquivos'])} | {len(r['emails'])} |"
           for r in log_envios if r["status"] == "enviado"]
    md += ["", "## Pendentes", ""] + ([f"- {b['cliente']} ({b['codigos']}): {'; '.join(b['faltas'])}" for b in bloqueados]
                                       or ["Nenhum."])
    with open(os.path.join(STATUS_DIR, f"ENVIOS_{comp}.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")


def main():
    args = dict(a.lstrip("-").split("=", 1) for a in sys.argv[1:] if "=" in a)
    comp = args.get("competencia") or fm.competencia_padrao(date.today())
    enviar, final = "--enviar" in sys.argv, "--final" in sys.argv
    if enviar and os.path.exists(os.path.join(STATUS_DIR, "ENVIO_PAUSADO")):
        log("ENVIO_PAUSADO presente em _status/ - nada enviado.")
        return
    log(f"início - competência {comp}, {'ENVIO REAL' if enviar else 'prévia'}{' (final)' if final else ''}")
    log(f"xlsx gerados: {fm.gerar_xlsx_relatorios(comp)}")
    caminho_log = os.path.join(STATUS_DIR, f"ENVIOS_{comp}.json")
    log_envios = json.load(open(caminho_log, encoding="utf-8")) if os.path.exists(caminho_log) else []
    ja = {frozenset(r["codigos"].split("+")) for r in log_envios if r["status"] == "enviado"}

    conn = psycopg2.connect(dbname="lifetools")
    if enviar:
        sincronizar_contatos(conn)
    grupos, st = montar_grupos(conn, comp)
    token, enviados_rodada, bloqueados, venc_ref = None, [], [], None
    for g in grupos:
        if frozenset(g["chave"].split("+")) in ja:
            log(f"JÁ ENVIADO  {g['cliente'][:40]:40} {g['chave']}")
            continue
        anexos, meta, faltas, emails, venc = checar_grupo(conn, g, comp, st)
        envio, zip_aviso, nz = preparar_anexos(anexos, g["chave"], comp) if anexos else ([], "", None)
        tam = sum(len(c) for _, _, c in envio)
        if tam > LIMITE_ENVIO_BYTES:
            faltas.append(f"anexo grande demais ({tam/1024/1024:.1f}MB) - precisa de envio por upload session")
        venc_ref = venc_ref or venc
        linha = f"{g['cliente'][:40]:40} {g['chave']:12} {len(anexos):2} arq {tam/1024/1024:5.2f}MB {len(emails)} e-mail(s)"
        if faltas:
            bloqueados.append({"cliente": g["cliente"] + (f" - {g['unidade']}" if g["unidade"] else ""),
                               "codigos": g["chave"], "faltas": faltas})
            log(f"BLOQUEADO   {linha} | {'; '.join(faltas)}")
            continue
        if not enviar:
            log(f"OK (prévia) {linha} venc={venc}")
            continue
        token = token or ed._get_graph_token(ed._get_graph_credenciais(os.environ["GCP_PROJECT"]))
        partes = [f"{MODALIDADE[c['modalidade']]} (contrato {c['codigo']})" for c in g["codigos"]]
        corpo = ed.montar_corpo_html(empresa_nome=g["cliente"], contratos_desc=" e ".join(partes),
                                     mes_referencia=ed._mes_referencia(venc), vencimento=venc.strftime("%d/%m/%Y"),
                                     tabela=ed.montar_tabela_html(meta), zip_aviso=zip_aviso)
        assunto = ed.montar_assunto(g["cliente"], venc.strftime("%d/%m/%Y"), g["unidade"])
        msg = {"message": {"subject": assunto, "body": {"contentType": "HTML", "content": corpo},
                           "toRecipients": [{"emailAddress": {"address": e}} for e in emails],
                           "attachments": [{"@odata.type": "#microsoft.graph.fileAttachment", "name": n,
                                            "contentType": ct, "contentBytes": base64.b64encode(c).decode()}
                                           for n, ct, c in envio]},
               "saveToSentItems": True}
        r = graph_send(token, msg)
        status = "enviado" if r.status_code == 202 else f"erro HTTP {r.status_code}"
        log(f"{status.upper():11} {linha}" + ("" if r.status_code == 202 else f" | {r.text[:200]}"))
        reg = {"cliente": g["cliente"], "codigos": g["chave"], "data_envio": datetime.now().strftime("%d/%m/%Y %H:%M"),
               "arquivos": [a[0] for a in anexos], "zip": nz, "emails": emails, "assunto": assunto, "status": status}
        log_envios.append(reg)
        with open(caminho_log, "w", encoding="utf-8") as f:
            json.dump(log_envios, f, ensure_ascii=False, indent=1)
        if status == "enviado":
            enviados_rodada.append(reg)
        else:
            bloqueados.append({"cliente": g["cliente"], "codigos": g["chave"], "faltas": [status]})
    conn.close()
    escrever_resumo(comp, log_envios, bloqueados)
    if enviar and (enviados_rodada or final):
        token = token or ed._get_graph_token(ed._get_graph_credenciais(os.environ["GCP_PROJECT"]))
        enviar_relatorio(token, comp, enviados_rodada, bloqueados, venc_ref)
    log(f"fim - {len(enviados_rodada)} enviado(s) nesta rodada, {len(bloqueados)} bloqueado(s)")


if __name__ == "__main__":
    main()
