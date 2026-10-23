# TXT 829 do Odonto Premium (Legado, Troca de Arquivos) para dependentes cujo titular já está ativo no Odonto.
# Dados: censo Full do portal (dependente e titular no contrato Saúde par) e alvo.json no diretório atual.
# Uso: montar_txt.py COD CENSO_FULL.csv... -> txt_<COD>.txt + txt_<COD>_fora.json (quem não deu pra montar e por quê)
import csv, json, sys, re, collections
import os
sys.path.insert(0, "/opt/lifetools/service")
AQUI = os.path.dirname(os.path.abspath(__file__))
from saneamento import layout
cod, censos = sys.argv[1], sys.argv[2:]
CNPJ = {"S7HNK": "62472070000108", "2M2DX": "62242628000150", "QTYQH": "62802942000140", "SY8MI": "37241124000411"}
UFC = {"RJ": "33", "SP": "35", "MG": "31", "ES": "32", "BA": "29", "SE": "28", "PE": "26"}
MUN = collections.defaultdict(dict)
for l in open(os.path.join(AQUI, "munic.csv"), encoding="latin-1"):
    if ";" in l:
        c, n = l.strip().split(";", 1); MUN[c[:2]][n.upper()] = c
REL = {"CONJUGE": "03", "FILHO(A)": "04", "ENTEADO": "06"}
EC = {"CASADO": "1", "SOLTEIRO": "2", "VIUVO": "3", "DESQUITADO": "4", "SEPARADO": "5", "OUTROS": "6"}
CEN = {}
for f in censos:
    t = open(f, encoding="latin-1").read().lstrip("ï»¿﻿")
    for r in csv.DictReader(t.splitlines(), delimiter=";"):
        if not r["DT_CANCELAMENTO"]: CEN[(r["EMPRESA"], r["CPF_DEPENDENTE"].zfill(11))] = r
# linha em branco com as constantes do Odonto Premium (plano 9732), como nos TXTs aceitos pelo Legado
base = " " * layout.TAMANHO_LINHA
for k, x in (("102", "1"), ("103", "15"), ("116", "0"), ("117", "9732"), ("118", "0"), ("121", "2"), ("122", "2"),
             ("124", "0"), ("130", "0"), ("132b", "1")):
    base = layout.gravar(base, k, x)
for k in ("132f", "132h1", "132h2", "132h3", "132h4", "132j", "132k", "132l", "132m", "132n", "132o", "132p"):
    base = layout.gravar(base, k, "")
iso = lambda d: f"{d[6:10]}{d[3:5]}{d[:2]}" if re.fullmatch(r"\d\d/\d\d/\d{4}", d or "") else ""
A = [a for a in json.load(open("alvo.json")) if a["p"] == cod and a["tit_atv"]]
linhas, fora = [], []
for a in A:
    rd, rt = CEN.get((a["s"], a["cpf"])), CEN.get((a["s"], a["tit"]))
    if not rd or not rt: fora.append((a["cpf"], a["nome"], "fora do censo")); continue
    rel = REL.get(rd["GRAU PARENTESCO"])
    if not rel: fora.append((a["cpf"], a["nome"], f"parentesco {rd['GRAU PARENTESCO']!r}")); continue
    if rel == "03" and not rd["DT_CASAMENTO"]: fora.append((a["cpf"], a["nome"], "cônjuge sem data de casamento")); continue
    mun = MUN[UFC.get(rt["UF_ENDERECO"], "")].get(rt["CIDADE"].upper())
    if not mun or not rt["CEP"]: fora.append((a["cpf"], a["nome"], "titular sem município/CEP")); continue
    v = {"101": str(len(linhas) + 1), "104": rt["MATRICULA"], "105": rd["USUARIO"].upper(), "106": iso(rd["DATA_NASCIMENTO"]),
         "107": "3" if rd["SEXO"] == "F" else "1", "108": a["cpf"], "111": rd["NOME MAE"].upper(), "119": iso(rd["DATA CADASTRO"]),
         "120": rel, "123": CNPJ[cod], "125": rt["LOGRADOURO"].upper(),
         "126": "".join(ch for ch in rt["NUMERO"] if ch.isdigit()) or "0", "127": rt["COMPLEMENTO"].upper(), "128": rt["BAIRRO"].upper(),
         "129": mun, "131": rt["CEP"].zfill(8), "132a": cod, "132c": a["tit"], "132d": iso(rt["DT_ADMISSAO"]),
         "132e": iso(rd["DT_CASAMENTO"]) if rel == "03" else "", "132g": EC.get(rd["ESTADO_CIVIL"], "2")}
    l = base
    for k, x in v.items(): l = layout.gravar(l, k, x)
    erros = layout.valida_linha(l)
    if erros: fora.append((a["cpf"], a["nome"], "valida: " + "; ".join(erros))); continue
    linhas.append(l)
open(f"txt_{cod}.txt", "w", encoding="latin-1", newline="").write("\n".join(linhas) + "\n")
json.dump(fora, open(f"txt_{cod}_fora.json", "w"), ensure_ascii=False, indent=1)
print(cod, "linhas", len(linhas), "fora", len(fora), fora)
