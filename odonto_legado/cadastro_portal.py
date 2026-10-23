# SÓ LEITURA: cadastro no portal moderno (beneficiaries?search -> beneficiaries/{code}) + titular (holderCode).
import sys, os, json
sys.path[:0] = ["/app", "/lifetools_service"]
from saneamento import coleta_portal as cp
ct = sys.argv[1]; cpfs = sys.argv[2].split(",")
out = {}
with cp.sessao(ct) as (page, h):
    for cpf in cpfs:
        det = cp.certificados_do_cpf(page, h, ct, cpf)
        tits = []
        for c in det or []:
            hc = c.get("holderCode")
            if hc and hc != c.get("code"):
                r = page.request.get(f"{cp.API}/beneficiaries/{hc}", headers=h)
                if r.ok: tits.append(r.json().get("content") or {})
        out[cpf] = {"det": det, "tit": tits}
        print(cpf, "ok" if det else det, len(tits), flush=True)
os.makedirs("/tmp/odo/cad", exist_ok=True)
json.dump(out, open(f"/tmp/odo/cad/{ct}.json", "w"), ensure_ascii=False, indent=1)
