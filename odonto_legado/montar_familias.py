# Famílias para formulário do Legado a partir do censo Full do portal (alvo.json no diretório atual, titular fora do Odonto).
# Uso: montar_familias.py COD CENSO_FULL.csv... -> fam_<COD>_novas.json (pula titulares já em lote_<COD>_*.json)
import csv, json, sys, collections
cod, censos = sys.argv[1], sys.argv[2:]
EC = {"CASADO": "1", "SOLTEIRO": "2", "VIUVO": "3", "DESQUITADO": "4", "SEPARADO": "5", "OUTROS": "6"}
TP = {"CONJUGE": "1", "FILHO(A)": "2", "ENTEADO": "12"}
CEN = {}
for censo in censos:
    t = open(censo, encoding="latin-1").read().lstrip("ï»¿﻿")
    for r in csv.DictReader(t.splitlines(), delimiter=";"):
        if not r["DT_CANCELAMENTO"]: CEN[(r["EMPRESA"], r["CPF_DEPENDENTE"].zfill(11))] = r
feitos = set()
for x in __import__("glob").glob(f"lote_{cod}_*.json"): feitos |= set(json.load(open(x)))
A = [a for a in json.load(open("alvo.json")) if a["p"] == cod and not a["tit_atv"] and not a["tit_pend"] and a["tit"] not in feitos]
fams, fora = {}, []
for a in A:
    tit = a["tit"]
    if not tit: fora.append((a["cpf"], "fora do censo")); continue
    fams.setdefault((a["s"], tit), []).append(a["cpf"])
out = []
for (s, tit), m in fams.items():
    rt = CEN.get((s, tit))
    if not rt or not rt["CEP"] or not rt["DT_ADMISSAO"]: fora.append((tit, "titular sem censo/CEP/admissão")); continue
    f = {"cpf": tit, "nome": rt["USUARIO"], "mae": rt["NOME MAE"], "sexo": rt["SEXO"] or "M", "ec": EC.get(rt["ESTADO_CIVIL"], "2"),
         "adm": rt["DT_ADMISSAO"].replace("/", ""), "tlog": rt["TIPO_LOGRADOURO"] or "R", "pEndereco": rt["LOGRADOURO"],
         "pNumero": "".join(ch for ch in rt["NUMERO"] if ch.isdigit()) or "0", "pCompl": rt["COMPLEMENTO"], "pBairro": rt["BAIRRO"],
         "pCidade": rt["CIDADE"], "pUf": rt["UF_ENDERECO"], "cep": rt["CEP"].zfill(8), "deps": []}
    for d in m:
        if d == tit: continue
        r = CEN.get((s, d)); tipo = TP.get(r["GRAU PARENTESCO"]) if r else None
        if not tipo: fora.append((d, "dependente sem parentesco no censo")); continue
        f["deps"].append({"cpf": d, "nome": r["USUARIO"], "nasc": r["DATA_NASCIMENTO"], "sexo": r["SEXO"] or "M",
                          "ec": EC.get(r["ESTADO_CIVIL"], "2"), "tipo": tipo, "mae": r["NOME MAE"], "cas": r["DT_CASAMENTO"] if tipo == "1" else ""})
    out.append(f)
json.dump(out, open(f"fam_{cod}_novas.json", "w"), ensure_ascii=False, indent=1)
print(cod, "familias", len(out), "vidas", sum(1 + len(x["deps"]) for x in out), "| fora:", fora)
