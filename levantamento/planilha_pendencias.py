"""Planilha única de pendências/críticas Hapvida desde 01/09 (26/10/2026, André): Moderno Saúde (fechar_contrato.relatorio)
+ Legado Saúde/Odonto Premium (Críticas Cadastrais + Inclusões Pendentes). Só o que NÃO está resolvido."""
import csv, glob, io, json, os, re, sys
from datetime import datetime
from pathlib import Path
sys.path[:0] = ["/app", "/lifetools_service"]
os.umask(0o077)
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from saneamento import layout
from saneamento.ferramentas import fechar_contrato as fc
import censo_legado_parser

MODERNO = sys.argv[1].split(",")
DESDE = "2026-09-01"
HOJE = datetime.now().strftime("%Y%m%d")
DEST = Path(f"/projeto_ra/movimentacoes/fechamento/LEVANTAMENTO_PENDENCIAS_{HOJE}")
TMP = Path("/tmp/lev")
cab = [x.value for x in load_workbook(fc.MODELO)["Modelo"][1]]
codigos = list(layout.CAMPOS)
saida = []   # (contrato, origem, linha829, status, descricao, referencia)

# ── Moderno Saúde ──
for ct in MODERNO:
    pasta = fc._pasta(ct)
    if not (pasta / "movs.json").exists():
        print(ct, "SEM COLETA"); continue
    movs = json.loads((pasta / "movs.json").read_text())
    pedidos = json.loads((pasta / "pedidos.json").read_text()) if (pasta / "pedidos.json").exists() else []
    if not movs and not pedidos:
        continue
    consulta = json.loads((pasta / "ativos.json").read_text())
    if any(v is None for v in consulta.values()):
        print(ct, "COLETA INCOMPLETA - fora"); continue
    nome_ = lambda x: x.get("nome") or ""
    ativos = {c: next(nome_(x) for x in v if x["status"] == "Ativo") for c, v in consulta.items()
              if v and any(x["status"] == "Ativo" for x in v)}
    rel, st = fc.relatorio(ct, movs, ativos, TMP / ct, ct, odonto=False)
    ws = load_workbook(rel)["Inclusões"]
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r[50]:   # Problema preenchido = não ativo e não em processamento
            l = " " * layout.TAMANHO_LINHA
            for k, v in zip(codigos, r[:49]):
                if v not in (None, ""):
                    l = layout.gravar(l, k, str(v))
            saida.append((ct, "Moderno Saúde", l, "Não ativo", r[50], r[51]))

# ── Legado: índice CPF -> linha 829 mais recente do Saúde (mesma pessoa) ──
idx = {}
for f in sorted(glob.glob("/dados_motor/*/*/original.txt")):
    try:
        cr = json.loads(open(os.path.join(os.path.dirname(f), "movimento.json")).read()).get("createdAt") or ""
    except Exception:
        cr = ""
    for l in open(f, encoding="latin1").read().splitlines():
        if len(l) >= 800 and layout.ler(l, "102") == "1" or len(l) >= 800:
            c = layout.ler(l.ljust(layout.TAMANHO_LINHA), "108")
            if c and (c not in idx or cr >= idx[c][0]):
                idx[c] = (cr, l.ljust(layout.TAMANHO_LINHA)[:layout.TAMANHO_LINHA])
so = lambda v: re.sub(r"\D", "", v or "").zfill(11) if re.sub(r"\D", "", v or "") else ""
ENVELOPE = re.compile(r"^MOVIMENTACAO CRITICADA$", re.I)
CASCATA = re.compile(r"EXISTE PENDENCIA VINCULADA", re.I)
ODONTO = set(os.environ.get("ODONTO", "").split(","))
for pasta in sorted(glob.glob("/dados_motor/_legado_pendencias/*")):
    cod = os.path.basename(pasta)
    try:
        ativos = {so(r["CPF_DEPENDENTE"]) for r in censo_legado_parser.parse_ativos_html(open(f"{pasta}/usuario.html", encoding="utf-8", errors="replace").read(), cod)}
    except Exception as e:
        print(cod, "lista de ativos ilegível:", e); ativos = None
    pessoas = {}
    for f in glob.glob(f"{pasta}/criticas_*.csv"):
        t = open(f, encoding="latin1").read(); i = t.find("CONTROLE;")
        for r in csv.DictReader(io.StringIO(t[i:]), delimiter=";"):
            c = so(r.get("CPF"))
            if not c or (r.get("STATUS") or "").upper() != "PENDENTE":
                continue
            p = pessoas.setdefault(c, {"nome": r.get("NOME"), "tit": so(r.get("CPF_TITULAR")), "plano": r.get("COD_PLANO"),
                                       "nasc": "", "desc": [], "ref": set()})
            for txt in ((r.get("COD_PENDENCIA") or "").strip() + " - " + (r.get("PENDENCIA") or "").strip(), r.get("CRITICA"), r.get("OBSERVACAO")):
                txt = re.sub(r"^\s*-\s*", "", (txt or "").strip())
                if txt and not ENVELOPE.search(re.sub(r"^\d+ - ", "", txt)) and txt not in p["desc"]:
                    p["desc"].append(txt)
            p["ref"].add(f"controle {r.get('CONTROLE')}")
    for f in glob.glob(f"{pasta}/pendentes_*.csv"):
        t = open(f, encoding="latin1").read().replace('="', '"')
        for r in csv.DictReader(io.StringIO(t), delimiter=";"):
            c = so(r.get("CPF"))
            if not c or (r.get("Status") or "").upper() == "PROCESSADO":
                continue
            p = pessoas.setdefault(c, {"nome": r.get("Nome"), "tit": so(r.get("CPF Titular")), "plano": "", "desc": [], "ref": set(),
                                       "nasc": r.get("Dt Nascimento") or ""})
            p["nasc"] = p["nasc"] or r.get("Dt Nascimento") or ""
            for txt in (f"Pedido {(r.get('Status') or '').lower()}: " + (r.get("Pendência") or "").strip(), r.get("Críticas"), r.get("Obervação")):
                txt = (txt or "").strip()
                if txt and txt not in p["desc"] and not txt.endswith(": -"):
                    p["desc"].append(txt)
            p["ref"].add(f"controle {r.get('Nu Controle')}")
    for c, p in pessoas.items():
        if ativos is not None and c in ativos:
            continue   # já ativo no contrato = resolvido
        l = idx.get(c, (None, None))[1]
        if l is None:
            l = " " * layout.TAMANHO_LINHA
            n = re.sub(r"\D", "", p["nasc"])
            for k, v in (("105", fc.nn(p["nome"])), ("108", c), ("132c", p["tit"] if p["tit"] != c else ""),
                         ("106", f"{n[4:8]}{n[2:4]}{n[:2]}" if len(n) == 8 else "")):
                if v:
                    l = layout.gravar(l, k, v)
        l = layout.gravar(l, "132a", cod)
        if p["plano"]:
            l = layout.gravar(l, "117", p["plano"])
        desc = [("Pendência da família no mesmo pedido (resolve junto com o titular/dependente pendenciado)" if CASCATA.search(d) else d) for d in p["desc"]]
        desc = list(dict.fromkeys(desc))
        saida.append((cod, "Legado Odonto Premium" if cod in ODONTO else "Legado Saúde", l,
                      "Não ativo" if ativos is not None else "Não conferido", " + ".join(desc) or "Pendente no portal - ver controle",
                      ", ".join(sorted(p["ref"]))))

# ── planilha ──
DEST.mkdir(parents=True, exist_ok=True)
wb = Workbook(); ws = wb.active; ws.title = "Pendências"
AZUL, CINZA, BR = PatternFill("solid", fgColor="1F4E78"), PatternFill("solid", fgColor="595959"), Font(color="FFFFFF", bold=True)
ws.append(cab + ["Status", "Descrição da pendência/crítica", "Contrato", "Origem", "Movimentação/Controle"])
for i, x in enumerate(ws[1], 1):
    x.fill, x.font = (AZUL if i <= 51 else CINZA), BR
for ct, origem, l, st, desc, ref in sorted(saida, key=lambda s: (s[1], s[0])):
    ws.append([layout.ler(l, k) or None for k in codigos] + [st, desc, ct, origem, ref])
for col, w in (("E", 34), ("AX", 12), ("AY", 80), ("AZ", 10), ("BA", 22), ("BB", 24)):
    ws.column_dimensions[col].width = w
ws.freeze_panes = "F2"; ws.auto_filter.ref = ws.dimensions
arq = DEST / f"PENDENCIAS_HAPVIDA_{HOJE}.xlsx"; wb.save(arq)
from collections import Counter
print("ARQUIVO", arq, len(saida), "linhas")
print(json.dumps(Counter((s[1], s[0]) for s in saida).most_common(), ensure_ascii=False))
