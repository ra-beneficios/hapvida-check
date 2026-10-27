import os, json, httpx, html
import enviar_documentos as ed
DEST = ["C-level@ra-beneficios.com.br", "financeiro@ra-beneficios.com.br", "relacionamento@ra-beneficios.com.br"]
assert all(d.lower().endswith("@ra-beneficios.com.br") for d in DEST)
log = [r for r in json.load(open("/sharepoint/faturas/_status/ENVIOS_OUT26.json")) if r["status"] == "enviado"]
n = len(log); titulo = f"Relatório de Envios — Faturamento Outubro/2026 ({n} clientes)"
td = 'style="padding:6px 10px;border:1px solid #d0d0d0;font-family:sans-serif;font-size:12px;vertical-align:top"'
th = 'style="padding:6px 10px;border:1px solid #d0d0d0;background:#1f3b5c;color:#fff;font-family:sans-serif;font-size:12px;text-align:left"'
linhas = ""
for r in log:
    arqs = "<br>".join(html.escape(a) for a in r["arquivos"])
    if r.get("zip"): arqs = f"<i>(compactados em {html.escape(r['zip'])})</i><br>" + arqs
    linhas += (f"<tr><td {td}>{html.escape(r['cliente'])}</td><td {td}>{r['codigos'].replace('+','<br>')}</td>"
               f"<td {td}>{r['data_envio']}</td><td {td}>{len(r['arquivos'])} arquivos:<br>{arqs}</td>"
               f"<td {td}>{'<br>'.join(html.escape(e) for e in r['emails'])}</td></tr>")
corpo = f"""<div style="font-family:sans-serif;color:#222">
<h2 style="font-size:18px">{titulo}</h2>
<p>Envio dos documentos de faturamento Hapvida (competência Outubro/2026, vencimento 10/10/2026) realizado em 30/09/2026 para {n} clientes.
Cada e-mail só foi enviado com todos os documentos completos (boleto e nota fiscal pareados, relatório analítico e lista de ativos).</p>
<table style="border-collapse:collapse;width:100%">
<thead><tr><th {th}>Cliente</th><th {th}>Código</th><th {th}>Data de Envio</th><th {th}>Arquivos Enviados</th><th {th}>E-mails enviados</th></tr></thead>
<tbody>{linhas}</tbody></table>
<p style="margin-top:16px"><b>Não enviados neste lote (aguardando documento da Hapvida ou cadastro):</b> LH Engenharia Brasília (ERQ43+VKTX1), LH Engenharia Sergipe (ICBJY+GRKKH), APJ Betim (Z8TUR) e Consórcio BR 493 (WARQU) — NF ainda não emitida; HECA Construtora (AT2BC) — NF e lista de ativos pendentes.</p>
<p style="color:#666;font-size:12px">Enviado por Cora - Assistente Digital - RA Benefícios</p></div>"""
tok = ed._get_graph_token(ed._get_graph_credenciais(os.environ["GCP_PROJECT"]))
msg = {"message": {"subject": titulo, "body": {"contentType": "HTML", "content": corpo},
       "toRecipients": [{"emailAddress": {"address": e}} for e in DEST]}, "saveToSentItems": True}
r = httpx.post(f"https://graph.microsoft.com/v1.0/users/{ed.SENDER}/sendMail",
               headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}, content=json.dumps(msg), timeout=60)
print("HTTP", r.status_code, r.text[:200], "|", titulo)
