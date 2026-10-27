"""Envio real OUT26 - 26 grupos completos. Uso: --dry (so' checa) | --enviar"""
import os, io, sys, json, base64, zipfile, httpx, psycopg2, unicodedata
from datetime import date, datetime
import enviar_documentos as ed

COMP = "OUT26"; MES_I, ANO_I = 10, 2026
LOG = f"/sharepoint/faturas/_status/ENVIOS_{COMP}.json"
GRUPOS = [  # (nome, pasta_empresa, [(codigo, modalidade)])
 ("AGECON INDUSTRIAL LTDA","AGECON INDUSTRIAL LTDA",[("YS5FT","Saúde")]),
 ("APJ ENGENHARIA E CONSTRUCOES LTDA","APJ ENGENHARIA E CONSTRUCOES LTDA",[("QX2M4","Saúde")]),
 ("ARTRIO ENGENHARIA LTDA","ARTRIO ENGENHARIA LTDA",[("W4HXL","Saúde"),("BZ3T5","Odonto Premium")]),
 ("C-MMIND CONST, MANUT E MONT INDUSTRIAL LTDA","C-MMIND CONST, MANUT E MONT INDUSTRIAL LTDA",[("P6TLX","Saúde"),("Y4VLX","Odonto Premium")]),
 ("CONSORCIO CONSTRUTOR EPC 08","CONSORCIO CONSTRUTOR EPC 08",[("WP64K","Saúde")]),
 ("CONSORCIO CONSTRUTOR EPC 10","CONSORCIO CONSTRUTOR EPC 10",[("2XP6W","Saúde")]),
 ("CONSORCIO CONSTRUTOR EPC 11","CONSORCIO CONSTRUTOR EPC 11",[("PEG8N","Saúde")]),
 ("CONSORCIO HC 2","CONSORCIO HC 2",[("4DFEV","Saúde"),("2M2DX","Odonto Premium")]),
 ("CONSORCIO HC 4","CONSORCIO HC 4",[("FHMWR","Saúde"),("QTYQH","Odonto Premium")]),
 ("CONSORCIO POSSEBON/UPE/CPL - EPC 13","CONSORCIO POSSEBON-UPE-CPL - EPC 13",[("8DAC9","Saúde"),("S7HNK","Odonto Premium")]),
 ("ENGECOLP ENGENHARIA E COMERCIO LTDA","ENGECOLP ENGENHARIA E COMERCIO LTDA",[("AQJZB","Saúde")]),
 ("G E M TRANSPORTE E LOCACOES LTDA","G E M TRANSPORTE E LOCACOES LTDA",[("13TQN","Saúde"),("BUZ7F","Odonto Premium")]),
 ("J BAIER LOCACOES E SERVICOS LTDA","J BAIER LOCACOES E SERVICOS LTDA",[("EM593","Saúde")]),
 ("JIREH TOPOGRAFIA LTDA","JIREH TOPOGRAFIA LTDA",[("YS55U","Saúde"),("611X2","Odonto Premium")]),
 ("JOMAGA ENGENHARIA SERV COMERCIO S.A","JOMAGA ENGENHARIA SERV COMERCIO S.A",[("XEIJL","Saúde")]),
 ("MPE ENGENHARIA E SERVICOS S/A","MPE ENGENHARIA E SERVICOS S-A",[("UEHRD","Saúde"),("BY3S8","Odonto Premium")]),
 ("NITNAVE MANUTENCAO SERV NAVAIS LTDA","NITNAVE MANUTENCAO SERV NAVAIS LTDA",[("NFUBS","Saúde")]),
 ("NORMATEL ENGENHARIA LTDA","NORMATEL ENGENHARIA LTDA",[("KGBQY","Saúde")]),
 ("PSD SOLUTIONS ENGENHARIA, SERVICOS E CONSULTORIA LTDA","PSD SOLUTIONS ENGENHARIA, SERVICOS E CONSULTORIA LTDA",[("IXP2R","Saúde"),("HH9PR","Odonto Premium")]),
 ("SINTRAMON SIND T E E M M INDUST MUNI ITABORAI","SINTRAMON SIND T E E M M INDUST MUNI ITABORAI",[("0KFXW","Saúde")]),
 ("STARK OIL & GAS MAINTENANCE LTDA","STARK OIL & GAS MAINTENANCE LTDA",[("X4ZAT","Saúde"),("IHWBW","Odonto Premium")]),
 ("THERMO SOLUTIONS MOTORES COMERCIO E SERVICOS LTDA","THERMO SOLUTIONS MOTORES COMERCIO E SERVICOS LTDA",[("CIJHG","Saúde")]),
 ("THREENG MANUTENCAO E SERVICOS LTDA","THREENG MANUTENCAO E SERVICOS LTDA",[("X98VE","Saúde")]),
 ("UTC OLEO E GAS ENGENHARIA S.A.",None,[("DAVJV","Saúde"),("SY8MI","Odonto Premium")]),
 ("VF DA ROSA REFEICOES LTDA","VF DA ROSA REFEICOES LTDA",[("G3W63","Saúde")]),
 ("WA CONSTRUCAO E ELETRICA LTDA","WA CONSTRUCAO E ELETRICA LTDA",[("5XQH7","Saúde"),("32I14","Odonto Premium")]),
]
PASTA_POR_COD = {"DAVJV": "UTC OLEO E GAS ENGENHARIA S.A.", "SY8MI": "UTC OLEO E GAS ENGENHARIA S.A"}

DESCR = [("BOLETO_COPARTICIPACAO","Boleto de cobrança — coparticipação"),("BOLETO_SAUDE","Boleto de cobrança — mensalidade saúde"),
 ("BOLETO","Boleto de cobrança — mensalidade"),("NFE_COPARTICIPACAO","Nota fiscal eletrônica de serviços — coparticipação"),
 ("NFE_SAUDE","Nota fiscal eletrônica de serviços — mensalidade saúde"),("NFE","Nota fiscal eletrônica de serviços — mensalidade"),
 ("RELATORIO_ANALITICO","Relatório analítico da fatura mensal"),
 ("COPARTICIPACAO_ANALITICO","Detalhe por beneficiário e procedimento dos valores de coparticipação do período"),
 ("BENEFICIARIOS_ATIVOS","Lista de beneficiários ativos do contrato"),("LISTA_ATIVOS","Lista de beneficiários ativos do contrato")]
def descricao(n, modal):
    for p, d in DESCR:
        if n.upper().startswith(p): return f"{d} ({modal}, formato {n.rsplit('.',1)[-1].upper()})."
    return None

db = psycopg2.connect(dbname="lifetools")
def q(sql, a):
    with db.cursor() as c: c.execute(sql, a); return c.fetchall()

def checar(grupo):
    nome, pasta_emp, cods = grupo
    anexos, meta, faltas, venc = [], [], [], None
    for cod, modal in cods:
        pasta = PASTA_POR_COD.get(cod, pasta_emp)
        arqs = ed.ler_pasta_sharepoint(pasta, cod, COMP)
        nomes = [a[0].upper() for a in arqs]
        tem = lambda p: any(n.startswith(p) for n in nomes)
        if not arqs: faltas.append(f"{cod}: pasta vazia"); continue
        faltas += [f"{cod}: {x}" for x in ed.checar_pares_boleto_nfe([a[0] for a in arqs])]
        if not any("ATIVOS" in n for n in nomes): faltas.append(f"{cod}: lista de ativos")
        for ext in ("PDF", "CSV"):
            if not any(n.startswith("RELATORIO_ANALITICO") and n.endswith(ext) for n in nomes): faltas.append(f"{cod}: relatório analítico {ext}")
        if modal == "Saúde":
            tipos = {bt for (bt,) in q("select f.bill_type from faturas f join empresa_operadora_codigos e on e.id=f.empresa_operadora_codigo_id where e.codigo_externo=%s and extract(month from f.vencimento)=%s and extract(year from f.vencimento)=%s", (cod, MES_I, ANO_I))}
            if "MENSALIDADE PLANO SAUDE" not in tipos: faltas.append(f"{cod}: sem mensalidade no banco")
            for p in ("BOLETO_SAUDE", "NFE_SAUDE"):
                if not tem(p): faltas.append(f"{cod}: {p}")
            if any(t != "MENSALIDADE PLANO SAUDE" for t in tipos):
                for p in ("BOLETO_COPARTICIPACAO", "NFE_COPARTICIPACAO"):
                    if not tem(p): faltas.append(f"{cod}: {p} (há copart no banco)")
            r = q("select min(f.vencimento) from faturas f join empresa_operadora_codigos e on e.id=f.empresa_operadora_codigo_id where e.codigo_externo=%s and f.bill_type='MENSALIDADE PLANO SAUDE' and extract(month from f.vencimento)=%s and extract(year from f.vencimento)=%s", (cod, MES_I, ANO_I))
            venc = venc or (r[0][0] if r else None)
        else:
            for p in ("BOLETO", "NFE"):
                if not tem(p): faltas.append(f"{cod}: {p}")
        for a in arqs:
            d = descricao(a[0], modal)
            if d is None: faltas.append(f"{cod}: arquivo inesperado {a[0]}"); continue
            anexos.append(a); meta.append({"nome": a[0], "descricao": d})
    emails = []
    for cod, _ in cods:
        for (es,) in q("select emails from contatos_faturamento where codigo_externo=%s", (cod,)):
            for e in es or []:
                if e.lower() not in emails: emails.append(e.lower())
    if not emails: faltas.append("sem e-mail cadastrado")
    if venc is None: faltas.append("vencimento não encontrado")
    return anexos, meta, faltas, emails, venc

def main():
    enviar = "--enviar" in sys.argv
    log = json.load(open(LOG)) if os.path.exists(LOG) else []
    ja = {r["cliente"] for r in log if r.get("status") == "enviado"}
    token = None
    for g in GRUPOS:
        nome, _, cods = g
        cod_str = "+".join(c for c, _ in cods)
        anexos, meta, faltas, emails, venc = checar(g)
        total = sum(len(c) for _, _, c in anexos)
        envio, zip_aviso = anexos, ""
        if total > ed.LIMITE_ANEXOS_BYTES:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for n, _, c in anexos: zf.writestr(n, c)
            nz = f"Documentos_{cod_str.replace('+','_')}_{COMP}.zip"
            envio = [(nz, "application/zip", buf.getvalue())]
            zip_aviso = (f"<p style='color:#666;font-size:12px'>Os {len(anexos)} arquivos abaixo somam {total/1024/1024:.1f}MB "
                         f"e foram compactados num único arquivo (<b>{nz}</b>) por conta do limite de tamanho de anexo do e-mail.</p>")
        tam = sum(len(c) for _, _, c in envio)
        if tam > 3.3 * 1024 * 1024: faltas.append(f"anexo grande demais ({tam/1024/1024:.1f}MB)")
        linha = f"{nome[:34]:34} {cod_str:12} {len(anexos):2} arq {tam/1024/1024:5.2f}MB {len(emails)} email venc={venc}"
        if faltas:
            print("BLOQUEADO", linha, "|", "; ".join(faltas)); continue
        if nome in ja:
            print("JA ENVIADO", linha); continue
        if not enviar:
            print("OK       ", linha); continue
        if token is None: token = ed._get_graph_token(ed._get_graph_credenciais(os.environ["GCP_PROJECT"]))
        partes = [f"{m} (contrato {c})" for c, m in cods]
        desc = partes[0] if len(partes) == 1 else " e ".join(partes)
        corpo = ed.montar_corpo_html(empresa_nome=nome, contratos_desc=desc, mes_referencia=ed._mes_referencia(venc),
                                     vencimento=venc.strftime("%d/%m/%Y"), tabela=ed.montar_tabela_html(meta), zip_aviso=zip_aviso)
        assunto = ed.montar_assunto(nome, venc.strftime("%d/%m/%Y"), ed._localidade(cods[0][0]))
        msg = {"message": {"subject": assunto, "body": {"contentType": "HTML", "content": corpo},
               "toRecipients": [{"emailAddress": {"address": e}} for e in emails],
               "attachments": [{"@odata.type": "#microsoft.graph.fileAttachment", "name": n, "contentType": ct,
                                "contentBytes": base64.b64encode(c).decode()} for n, ct, c in envio]}, "saveToSentItems": True}
        r = httpx.post(f"https://graph.microsoft.com/v1.0/users/{ed.SENDER}/sendMail",
                       headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, content=json.dumps(msg), timeout=180)
        st = "enviado" if r.status_code == 202 else f"erro HTTP {r.status_code}"
        print(st.upper(), linha, "" if r.status_code == 202 else r.text[:200])
        log.append({"cliente": nome, "codigos": cod_str, "data_envio": datetime.now().strftime("%d/%m/%Y %H:%M"),
                    "arquivos": [a[0] for a in anexos], "zip": envio[0][0] if envio is not anexos else None,
                    "emails": emails, "assunto": assunto, "status": st})
        json.dump(log, open(LOG, "w"), ensure_ascii=False, indent=1)
main()
