"""
Complemento automático da coparticipação (André, 17/11/2026: "diária do 27 ao 10, complemento automático").

Portal novo e Legado (Saúde do Legado: Z8TUR, L&H ERQ43/ICBJY — copart = 2º boleto do mês no status diário das
06:30, legado_auto --status; documentos pela Troca de Arquivos/Bemobi/DANFSe, legado_auto.baixar_contrato).

A Hapvida às vezes emite a fatura de coparticipação DEPOIS que o faturamento do contrato já foi enviado ao cliente
(NOV26: DAVJV R$ 21.443,40 apareceu ~30/10, X98VE e YXF01 também — os três saíram em 27/10 sem a copart, e o motor
não voltava em grupo já enviado). Esta rotina roda todo dia, do dia 27 ao dia 10:

1. contratos Saúde com fatura de copart na competência (banco, atualizado pelas rotinas das 06:45/06:55) cujo grupo
   já foi enviado e nenhum envio levou a copart;
2. baixa os documentos do contrato (o mesmo caminho do faturamento: enviar_documentos.montar_e_enviar sem e-mail);
   sem o CSV da Hapvida, o XLSX sai do PDF oficial (fm.gerar_xlsx_pasta);
3. confere como o envio do mês: lista fechada da copart (boleto, NF, analítico PDF+XLSX), total do relatório = boleto,
   relatório do mês; NF que não saiu não segura a partir do dia 5 (mesma regra do envio final);
4. manda ao cliente SÓ a copart, para os contatos do grupo, e registra em ENVIOS_<COMP>.json (status "complemento");
5. avisa o time interno (RELATORIO_PARA) do que saiu e, no dia 10, do que ainda não deu para mandar.

Uso (no container): python3 complemento_copart.py [--competencia=DEZ26] [--enviar]   (sem --enviar = prévia)
Respeita _status/ENVIO_PAUSADO e _status/FATURAMENTO_PAUSADO.
"""
import base64
import html
import json
import os
import sys
from datetime import date, datetime

import psycopg2

import enviar_documentos as ed
import envio_faturamento as ef
import faturamento_mensal as fm
import padrao_faturamento as pf

ULTIMO_DIA = 10          # vencimento da copart: depois disso não manda mais sozinho
NF_NAO_SEGURA_DESDE = 5  # como o envio final do dia 05: NF que não saiu vai como pendência
STATUS_QUE_LEVAM = ("enviado", "reenviado", "complemento")


def no_periodo(hoje: date) -> bool:
    return hoje.day >= 27 or hoje.day <= ULTIMO_DIA


def levou_copart(log_envios: list[dict], cod: str) -> bool:
    marca = f"_COPARTICIPACAO_{cod}_".upper()
    return any(r.get("status") in STATUS_QUE_LEVAM and any(marca in a.upper() for a in r.get("arquivos") or [])
               for r in log_envios)


def pendentes(log_envios: list[dict], saude: dict[str, set[str]]) -> list[tuple[str, dict]]:
    """(codigo, registro do envio original) de cada contrato com copart na competência, grupo já enviado e copart
    nunca enviada. Grupo ainda não enviado fica com o envio do mês (vai tudo junto)."""
    out = []
    for cod, tipos in sorted(saude.items()):
        if "COPARTICIPACAO" not in tipos or levou_copart(log_envios, cod):
            continue
        orig = next((r for r in log_envios if r.get("status") == "enviado" and cod in r["codigos"].split("+")), None)
        if orig:
            out.append((cod, orig))
    return out


def copart_legado(comp: str) -> dict[str, dict]:
    """Saúde do Legado com copart na competência: 2 ou mais boletos do mês no status (mensalidade + copart) - a
    mesma regra do envio do mês (envio_faturamento.checar_grupo). codigo -> {"empresa", "valores"}."""
    legado = fm.contratos_legado()
    mods = fm.modalidades(set(legado)) if legado else {}
    st = fm.status_legado(comp)
    return {c: {"empresa": legado[c], "valores": st[c]["valores"]} for c in legado
            if pf.PLANO_DA_MODALIDADE.get(mods.get(c)) == "SAUDE" and c in st and len(st[c]["boletos"]) >= 2}


def anexos_copart(pasta: str, cod: str, comp: str, valores, hoje: date):
    """(anexos, faltas, pendencias) só da copart, com as checagens do envio do mês."""
    nomes = fm.arquivos(pasta)
    esperados = [n for n in pf.esperados(cod, comp, "SAUDE", True) if "_COPARTICIPACAO_" in n]
    tem = {n.upper(): n for n in nomes}
    faltas = [n for n in esperados if n.upper() not in tem]
    faltas += [p for p in pf.conferir_valores(pasta, cod, comp, "SAUDE", True, valores) if "COPARTICIPACAO" in p]
    rel = pf.nome("RELATORIO_ANALITICO", "COPARTICIPACAO", cod, comp, "pdf").upper()
    if rel in tem and not ef.copart_do_mes(os.path.join(pasta, tem[rel]), comp):
        faltas.append(f"relatório de coparticipação PDF é de outro mês ({tem[rel]})")
    pendencias = []
    nf = pf.nome("NFE", "COPARTICIPACAO", cod, comp, "pdf")
    if nf in faltas and hoje.day >= NF_NAO_SEGURA_DESDE and hoje.day <= ULTIMO_DIA:
        faltas.remove(nf)
        pendencias.append(nf)
    anexos = []
    for n in esperados:
        if n.upper() in tem:
            with open(os.path.join(pasta, tem[n.upper()]), "rb") as f:
                anexos.append((tem[n.upper()], ed.mimetypes.guess_type(n)[0] or "application/octet-stream", f.read()))
    return anexos, faltas, pendencias


def _emails(conn, codigos: list[str]) -> list[str]:
    emails = []
    with conn.cursor() as cur:
        for c in codigos:
            cur.execute("SELECT emails FROM contatos_faturamento WHERE codigo_externo = %s", (c,))
            for (es,) in cur.fetchall():
                emails += [e.lower() for e in es or [] if e.lower() not in emails]
    return emails


def _venc_copart(conn, cod: str, comp: str) -> str | None:
    mes, ano = fm.competencia_para_mes(comp)
    with conn.cursor() as cur:
        cur.execute("""SELECT min(f.vencimento) FROM faturas f JOIN empresa_operadora_codigos e
                       ON e.id = f.empresa_operadora_codigo_id WHERE e.codigo_externo = %s
                       AND f.bill_type <> 'MENSALIDADE PLANO SAUDE'
                       AND extract(month FROM f.vencimento) = %s AND extract(year FROM f.vencimento) = %s""",
                    (cod, mes, ano))
        v = cur.fetchone()[0]
    return v.strftime("%d/%m/%Y") if v else None


def _venc_pdf(pasta: str, cod: str, comp: str) -> str | None:
    """Legado não tem a fatura no banco: o vencimento sai do analítico oficial (PDF)."""
    caminho = os.path.join(pasta, pf.nome("RELATORIO_ANALITICO", "COPARTICIPACAO", cod, comp, "pdf"))
    try:
        return pf.copart_pdf_para_linhas(caminho)["vencimento"] if os.path.exists(caminho) else None
    except ValueError:
        return None


def corpo_cliente(orig: dict, cod: str, venc: str, meta: list[dict], zip_aviso: str) -> str:
    return f"""
    <div style="font-family:sans-serif;color:#222">
      <p>Olá,</p>
      <p>Complementando o envio de faturamento de {html.escape(orig['data_envio'][:10])}, seguem os documentos da
      <b>coparticipação</b> de <b>{html.escape(orig['cliente'])}</b> — Saúde (contrato {cod}), com vencimento em
      <b>{html.escape(venc)}</b>, disponibilizados pela operadora após aquele envio.
      Qualquer dúvida, entre em contato com seu executivo de Relacionamento.</p>
      {zip_aviso}
      {ed.montar_tabela_html(meta)}
      <p style="color:#666;font-size:12px">Enviado por Cora - Assistente Digital - RA Benefícios</p>
    </div>"""


def corpo_interno(comp: str, enviados: list[dict], bloqueados: list[dict], ultimo_dia: bool) -> str:
    linhas = "".join(f"<tr><td>{html.escape(r['cliente'])}</td><td>{r['codigos']}</td><td>{r['data_envio']}</td>"
                     f"<td>{'; '.join(r['pendencias']) or '—'}</td></tr>" for r in enviados)
    bloq = "".join(f"<tr><td>{html.escape(b['cliente'])}</td><td>{b['codigo']}</td>"
                   f"<td>{html.escape('; '.join(b['faltas']))}</td></tr>" for b in bloqueados)
    th = "style='text-align:left;padding:4px 10px;background:#1F4E78;color:#fff'"
    partes = ["<div style='font-family:sans-serif;color:#222'>",
              f"<p>Complemento de coparticipação — competência <b>{comp}</b> (faturas emitidas pela Hapvida "
              "depois do envio do faturamento).</p>"]
    if enviados:
        partes.append(f"<p><b>Enviados ao cliente</b></p><table style='border-collapse:collapse'><tr><th {th}>Cliente</th>"
                      f"<th {th}>Contrato</th><th {th}>Enviado em</th><th {th}>Pendência</th></tr>{linhas}</table>")
    if bloqueados:
        titulo = ("Não enviados até o vencimento — alinhar com o cliente" if ultimo_dia
                  else "Ainda não enviados (nova tentativa amanhã)")
        partes.append(f"<p><b>{titulo}</b></p><table style='border-collapse:collapse'><tr><th {th}>Cliente</th>"
                      f"<th {th}>Contrato</th><th {th}>O que falta</th></tr>{bloq}</table>")
    partes.append("<p style='color:#666;font-size:12px'>Enviado por Cora - Assistente Digital - RA Benefícios</p></div>")
    return "".join(partes)


def main():
    args = dict(a.lstrip("-").split("=", 1) for a in sys.argv[1:] if "=" in a)
    hoje = date.today()
    comp = args.get("competencia") or fm.competencia_padrao(hoje)
    enviar = "--enviar" in sys.argv
    if not args.get("competencia") and not no_periodo(hoje):
        ef.log(f"complemento copart: fora do período (27 ao {ULTIMO_DIA}) - nada a fazer")
        return
    for trava in ("ENVIO_PAUSADO", "FATURAMENTO_PAUSADO"):
        if enviar and os.path.exists(os.path.join(ef.STATUS_DIR, trava)):
            ef.log(f"complemento copart: {trava} presente em _status/ - nada feito")
            return
    caminho_log = os.path.join(ef.STATUS_DIR, f"ENVIOS_{comp}.json")
    if not os.path.exists(caminho_log):
        ef.log(f"complemento copart: {comp} ainda sem envio - nada a fazer")
        return
    log_envios = json.load(open(caminho_log, encoding="utf-8"))
    saude = fm.contratos_saude(comp)
    legado = {c: v for c, v in copart_legado(comp).items() if c not in saude}
    lista = pendentes(log_envios, {**saude, **{c: {"SAUDE", "COPARTICIPACAO"} for c in legado}})
    ef.log(f"complemento copart {comp}: {len(lista)} contrato(s) com copart emitida depois do envio"
           + (f" ({', '.join(c for c, _ in lista)})" if lista else ""))
    if not lista:
        return

    project = os.environ["GCP_PROJECT"]
    creds = {"carol": fm.scraper.get_credentials(project, "hapvida-portal-saude"),
             "marco": fm.scraper.get_credentials(project, "hapvida-portal-saude")}  # as duas = Cora (15/10/2026)
    avisos = {}
    do_portal_novo = {c: saude[c] for c, _ in lista if c in saude}
    if do_portal_novo:
        try:
            avisos = fm.baixar_documentos_saude(do_portal_novo, comp, "completo", creds)
        except Exception as e:
            ef.log(f"complemento copart: download do portal novo falhou ({e}) - confere o que já estiver na pasta")
    do_legado = [c for c, _ in lista if c in legado]
    if do_legado:
        import legado_auto
        mes, ano = fm.competencia_para_mes(comp)
        assumidos, creds_legado = fm.legado_assumido(fm.contratos_legado())
        for c in do_legado:
            if c not in assumidos:
                avisos[c] = ["sem credencial válida do Legado no Bitwarden"]
                continue
            try:
                r = legado_auto.baixar_contrato(c, legado[c]["empresa"], creds_legado[c], comp, mes, ano,
                                                fm.pasta_contrato(c, comp, legado[c]["empresa"]), "SAUDE")
                avisos[c] = r.get("avisos") or []
            except Exception as e:
                avisos[c] = [f"download do Legado falhou: {e}"]
    for c, _ in lista:
        fm.gerar_xlsx_pasta(fm.pasta_contrato(c, comp, legado[c]["empresa"]) if c in legado else fm.pasta_contrato(c, comp))

    conn = psycopg2.connect(dbname="lifetools")
    token, enviados, bloqueados = None, [], []
    for cod, orig in lista:
        if cod in legado:
            pasta = fm.pasta_contrato(cod, comp, legado[cod]["empresa"])
            valores = legado[cod]["valores"]  # boletos do mês no status (o total tem que bater com um deles)
        else:
            pasta, valores = fm.pasta_contrato(cod, comp), fm.valores_saude(cod, comp)
        anexos, faltas, pendencias = anexos_copart(pasta, cod, comp, valores, hoje)
        emails = _emails(conn, orig["codigos"].split("+"))
        venc = _venc_copart(conn, cod, comp) or _venc_pdf(pasta, cod, comp)
        if not venc:
            faltas.append("vencimento da coparticipação não encontrado")
        if not emails:
            faltas.append("sem e-mail cadastrado")
        envio, zip_aviso, nz = ef.preparar_anexos(anexos, cod, comp) if anexos else ([], "", None)
        if sum(len(c) for _, _, c in envio) > ef.LIMITE_ENVIO_BYTES:
            faltas.append("anexos acima de 10MB mesmo compactados")
        if faltas:
            ef.log(f"complemento copart {cod}: BLOQUEADO | {'; '.join(faltas)} | avisos: {avisos.get(cod)}")
            bloqueados.append({"cliente": orig["cliente"], "codigo": cod, "faltas": faltas})
            continue
        assunto = f"Complemento – {orig.get('assunto') or ed.montar_assunto(orig['cliente'], venc)}"
        meta = [{"nome": n, "descricao": ef.descricao(n, "Saúde")} for n, _, _ in anexos]
        ef.log(f"complemento copart {cod}: pronto | {len(anexos)} arq | para {emails} | {assunto}"
               + (f" | sem: {'; '.join(pendencias)}" if pendencias else ""))
        if not enviar:
            continue
        token = token or ed._get_graph_token(ed._get_graph_credenciais(project))
        r = ef.graph_send(token, {"message": {
            "subject": assunto, "body": {"contentType": "HTML", "content": corpo_cliente(orig, cod, venc, meta, zip_aviso)},
            "toRecipients": [{"emailAddress": {"address": e}} for e in emails],
            "attachments": [{"@odata.type": "#microsoft.graph.fileAttachment", "name": n, "contentType": ct,
                             "contentBytes": base64.b64encode(c).decode()} for n, ct, c in envio]},
            "saveToSentItems": True})
        ef.log(f"complemento copart {cod}: HTTP {r.status_code}" + ("" if r.status_code == 202 else f" {r.text[:200]}"))
        if r.status_code != 202:
            bloqueados.append({"cliente": orig["cliente"], "codigo": cod, "faltas": [f"erro HTTP {r.status_code} no envio"]})
            continue
        reg = {"cliente": orig["cliente"], "codigos": orig["codigos"], "data_envio": datetime.now().strftime("%d/%m/%Y %H:%M"),
               "arquivos": [n for n, _, _ in anexos], "zip": nz, "emails": emails, "assunto": assunto,
               "status": "complemento", "tipo": "coparticipacao", "pendencias": pendencias}
        log_envios.append(reg)
        with open(caminho_log, "w", encoding="utf-8") as f:
            json.dump(log_envios, f, ensure_ascii=False, indent=1)
        enviados.append(reg)
    conn.close()

    ultimo = hoje.day == ULTIMO_DIA
    if enviar and (enviados or (bloqueados and ultimo)):
        token = token or ed._get_graph_token(ed._get_graph_credenciais(project))
        r = ef.graph_send(token, {"message": {
            "subject": f"Complemento de coparticipação – {comp}" + (" | pendências no vencimento" if ultimo and bloqueados else ""),
            "body": {"contentType": "HTML", "content": corpo_interno(comp, enviados, bloqueados, ultimo)},
            "toRecipients": [{"emailAddress": {"address": e}} for e in ef.RELATORIO_PARA]}, "saveToSentItems": True})
        ef.log(f"complemento copart: aviso interno HTTP {r.status_code}")
    ef.log(f"complemento copart {comp}: fim - {len(enviados)} enviado(s), {len(bloqueados)} bloqueado(s)")


if __name__ == "__main__":
    main()
