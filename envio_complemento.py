"""
Complemento de faturamento: manda ao cliente SO' o arquivo que ficou faltando
num envio ja' feito (o resto ja' foi). Criado 03/11/2026 pro HC 4 NOV26: o
grupo FHMWR+QTYQH saiu sem o analitico PDF do Odonto (QTYQH), com nota ao
cliente "sera' enviado ate' 05/11" - o motor (envio_faturamento) pula grupo ja'
enviado, entao o complemento tem que sair por aqui.

Uso (no container):
  python3 envio_complemento.py --competencia=NOV26 --codigo=QTYQH --arquivo=RELATORIO_ANALITICO_ODONTO_QTYQH_NOV26.pdf --prazo=05/11/2026 [--enviar]

- Legado sem o arquivo na pasta: tenta baixar de novo (legado_auto.baixar_contrato).
- Achou: e-mail so' com esse arquivo pros contatos do grupo (contatos_faturamento),
  registra no ENVIOS_<COMP>.json (status "complemento") e tira a pendencia do envio original.
- Idempotente: complemento ja' enviado nao sai de novo.
- Nao achou e chegou o prazo: avisa o time interno (RELATORIO_PARA) uma vez.
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


def main():
    args = dict(a.lstrip("-").split("=", 1) for a in sys.argv[1:] if "=" in a)
    comp, cod, arq = args["competencia"], args["codigo"].upper(), args["arquivo"]
    prazo = datetime.strptime(args["prazo"], "%d/%m/%Y").date()
    enviar = "--enviar" in sys.argv
    caminho_log = os.path.join(ef.STATUS_DIR, f"ENVIOS_{comp}.json")
    log_envios = json.load(open(caminho_log, encoding="utf-8"))
    orig = next((r for r in log_envios if r["status"] == "enviado" and cod in r["codigos"].split("+")), None)
    if not orig:
        ef.log(f"complemento {cod}: grupo ainda não enviado em {comp} - o motor manda tudo junto, nada a fazer")
        return
    if any(r["status"] == "complemento" and arq in r["arquivos"] for r in log_envios):
        ef.log(f"complemento {cod}: {arq} já enviado - nada a fazer")
        return
    pasta = fm.pasta_contrato(cod, comp)
    caminho = os.path.join(pasta, arq)
    legado = fm.contratos_legado()
    if not os.path.exists(caminho) and cod in legado:
        import legado_auto
        mes, ano = fm.competencia_para_mes(comp)
        _, creds = fm.legado_assumido(legado)
        plano = "SAUDE" if "_SAUDE_" in arq.upper() else "ODONTO"
        legado_auto.baixar_contrato(cod, legado[cod], creds[cod], comp, mes, ano, pasta, plano)
    token = None
    if not os.path.exists(caminho):
        ef.log(f"complemento {cod}: {arq} ainda não disponível")
        avisado = any(r["status"] == "complemento: aviso de prazo" and arq in r["arquivos"] for r in log_envios)
        if enviar and date.today() >= prazo and not avisado:
            token = ed._get_graph_token(ed._get_graph_credenciais(os.environ["GCP_PROJECT"]))
            corpo = (f"<div style='font-family:sans-serif;color:#222'><p>O arquivo <b>{html.escape(arq)}</b> "
                     f"({html.escape(orig['cliente'])}, contrato {cod}, competência {comp}) ainda não foi "
                     f"disponibilizado pela operadora. O cliente foi informado de que seria enviado até "
                     f"<b>{prazo:%d/%m/%Y}</b>. Favor alinhar com o cliente.</p></div>")
            r = ef.graph_send(token, {"message": {"subject": f"Complemento de faturamento pendente – {orig['cliente']} | {comp}",
                                                  "body": {"contentType": "HTML", "content": corpo},
                                                  "toRecipients": [{"emailAddress": {"address": e}} for e in ef.RELATORIO_PARA]},
                                      "saveToSentItems": True})
            ef.log(f"complemento {cod}: aviso de prazo ao time interno HTTP {r.status_code}")
            if r.status_code == 202:
                log_envios.append({"cliente": orig["cliente"], "codigos": orig["codigos"], "arquivos": [arq],
                                   "data_envio": datetime.now().strftime("%d/%m/%Y %H:%M"), "zip": None,
                                   "emails": ef.RELATORIO_PARA, "status": "complemento: aviso de prazo", "pendencias": []})
                json.dump(log_envios, open(caminho_log, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return
    conn = psycopg2.connect(dbname="lifetools")
    emails = []
    with conn.cursor() as cur:
        for c in orig["codigos"].split("+"):
            cur.execute("SELECT emails FROM contatos_faturamento WHERE codigo_externo = %s", (c,))
            for (es,) in cur.fetchall():
                emails += [e.lower() for e in es or [] if e.lower() not in emails]
    conn.close()
    modal = "Odonto Premium" if "_ODONTO_" in arq.upper() else "Saúde"
    venc = orig["assunto"].rsplit("Vencimento", 1)[-1].strip()
    corpo = f"""
    <div style="font-family:sans-serif;color:#222">
      <p>Olá,</p>
      <p>Conforme informado no envio de {html.escape(orig['data_envio'][:10])}, segue o documento que completa o
      faturamento de <b>{html.escape(orig['cliente'])}</b> — {modal} (contrato {cod}), com vencimento em <b>{html.escape(venc)}</b>.
      Qualquer dúvida, entre em contato com seu executivo de Relacionamento.</p>
      {ed.montar_tabela_html([{"nome": arq, "descricao": ef.descricao(arq, modal)}])}
      <p style="color:#666;font-size:12px">Enviado por Cora - Assistente Digital - RA Benefícios</p>
    </div>"""
    assunto = f"Complemento – {orig['assunto']}"
    ef.log(f"complemento {cod}: {arq} disponível | para {emails} | assunto: {assunto}")
    if not enviar or not emails:
        ef.log("complemento: prévia, nada enviado" if emails else "complemento: sem e-mail cadastrado")
        return
    token = ed._get_graph_token(ed._get_graph_credenciais(os.environ["GCP_PROJECT"]))
    with open(caminho, "rb") as f:
        dados = f.read()
    r = ef.graph_send(token, {"message": {"subject": assunto, "body": {"contentType": "HTML", "content": corpo},
                                          "toRecipients": [{"emailAddress": {"address": e}} for e in emails],
                                          "attachments": [{"@odata.type": "#microsoft.graph.fileAttachment", "name": arq,
                                                           "contentType": "application/pdf" if arq.lower().endswith(".pdf")
                                                           else "application/octet-stream",
                                                           "contentBytes": base64.b64encode(dados).decode()}]},
                              "saveToSentItems": True})
    ef.log(f"complemento {cod}: HTTP {r.status_code} {r.text[:200]}")
    if r.status_code == 202:
        orig["pendencias"] = [p for p in orig.get("pendencias", []) if not p.endswith(arq)]
        log_envios.append({"cliente": orig["cliente"], "codigos": orig["codigos"], "arquivos": [arq], "zip": None,
                           "data_envio": datetime.now().strftime("%d/%m/%Y %H:%M"), "emails": emails,
                           "assunto": assunto, "status": "complemento", "pendencias": []})
        json.dump(log_envios, open(caminho_log, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
