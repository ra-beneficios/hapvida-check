"""Incluir um dependente no pedido PENDENTE do titular, como a tela "Inclusão de dependentes" da Hapvida (10/11/2026).

André (MOV-4 l.8, Magnolia, pedido 68953885 do Almir): a Hapvida não tem pedido dela — ela entra como dependente no
pedido pendente do titular. Aprovado 10/11: o pós-vendas responde a declaração de saúde no chamado (copiando da
declaração assinada) e anexa os documentos; o robô inclui. O robô NUNCA responde a declaração sozinho.

Contrato da tela (main.229a5ef7.js, lido 10/11):
  GET  forms/partner-company/{CT}/health-declaration?holderDocument=   (as perguntas: code, description, complement)
  GET  life-screen/control-number/{cn}                                  (titular: plano, vigência, unidade)
  GET  life-screen/control-number/{cn}/dependents                       (dependentes já no pedido)
  GET  life-screen/control-number/{cn}/edition-lock
  PUT  life-screen/control-number/{cn}/dependents/pendencies            (pedido Pendente: modo with-pendency)
  DELETE life-screen/control-number/{cn}/edition-lock
O item novo vai com operationType Added (1) — enum QO da tela: NotChanged 0, Added 1, Updated 2, Deleted 3; os que
já estão no pedido vão NotChanged, iguais (mesmo mapeamento do pedido_pendencia).
Depois do PUT o pedido é lido de novo e o CPF tem que estar lá. Não devolve à validação aqui: os documentos da
dependente vão aos espaços dela pelo caminho normal (anexar) quando a conferência achar o pedido.

    python3 pedido_dependente.py perguntas CONTRATO CPF_TITULAR
"""
import json
import re
import sys

sys.path[:0] = ["/app", "/lifetools_service"]
from saneamento import coleta_portal  # noqa: E402
import pedido_pendencia as pp  # noqa: E402

API = coleta_portal.API
LS = pp.LS
ADDED = 1
# layout 120 (planilha) -> tipo de dependente da tela (forms/partner-company/{CT}/dependent-types)
TIPO_DEPENDENTE = {"03": "1", "04": "2", "06": "12", "08": "3", "10": "5"}


def _cpf(x) -> str:
    return re.sub(r"\D", "", str(x or "")).zfill(11)


def perguntas_de(conteudo: dict) -> list[dict]:
    return [{"numero": str(q["code"]), "titulo": q.get("description") or "", "texto": q.get("complement") or ""}
            for q in conteudo.get("data") or [] if q.get("code") and q.get("isDisplay", True)]


def perguntas(contrato: str, cpf_titular: str) -> list[dict]:
    with coleta_portal.sessao(contrato) as (page, h):
        r = page.request.get(f"{API}/movements/forms/partner-company/{contrato}/health-declaration?holderDocument={_cpf(cpf_titular)}",
                             headers=h, timeout=90000)
        if not r.ok:
            raise RuntimeError(f"Hapvida respondeu {r.status} nas perguntas da declaração: {r.text()[:200]}")
        return perguntas_de(r.json().get("content") or {})


def validar(novo: dict, lista: list[dict]) -> list[str]:
    """O que falta para a inclusão sair (nada é inventado: sem o dado, não inclui)."""
    falta = [c for c in ("nome", "cpf", "nascimento", "sexo", "mae", "tipo", "estado_civil") if not novo.get(c)]
    if novo.get("tipo") == "1" and not novo.get("casamento"):
        falta.append("casamento")
    resp = {str(d.get("questionNumber")): d for d in novo.get("declaracao") or []}
    falta += [f"pergunta {q['numero']}" for q in lista if q["numero"] not in resp]
    falta += [f"descrição da pergunta {n}" for n, d in resp.items() if d.get("response") and not (d.get("description") or "").strip()]
    return falta


def montar(deps: dict, holder: dict, novo: dict, contrato: str, quem: dict) -> dict:
    """Payload do PUT dependents/pendencies: os do pedido como estão + o novo (Added)."""
    h = holder.get("holder") or {}
    hc, hn = str(holder.get("controlNumber") or deps.get("holderCode") or ""), h.get("name") or deps.get("holderName") or ""
    plano = h.get("plan") or {}
    dental_company = (plano.get("dentalPlan") or {}).get("dentalCompanyCode") or deps.get("dentalCompanyCode")
    itens = [pp._dependente(d, hc, hn, dental_company, pp.NOT_CHANGED) for d in deps.get("dependents") or []]
    dep = {"name": novo["nome"], "birthDate": pp._data_iso(novo["nascimento"]), "document": _cpf(novo["cpf"]),
           "gender": novo["sexo"], "civilStatus": int(novo["estado_civil"]), "mothersName": novo["mae"],
           "marriageDate": pp._data_iso(novo.get("casamento")) if novo.get("casamento") else None,
           "beneficiaryType": {"beneficiaryTypeCode": novo["tipo"]},
           "healthDeclarations": [{"questionNumber": str(d["questionNumber"]), "response": bool(d.get("response")),
                                   "description": d.get("description") or ""} for d in novo.get("declaracao") or []],
           "plan": {"startDate": plano.get("startDate"), "healthPlan": {"healthPlanCode": (plano.get("healthPlan") or {}).get("healthPlanCode")}},
           "contact": {}}
    item = pp._dependente(dep, hc, hn, dental_company, ADDED)
    item.pop("code", None)
    item["sequentialNumber"] = len(itens) + 1
    itens.append(item)
    corpo = {"dependents": itens, "holderCode": pp._bn(hc), "holderDocument": pp._bn(_cpf(h.get("document") or deps.get("holderDocument"))),
             "beneficiaryCode": pp._bn(h.get("beneficiaryCode") or deps.get("holderBeneficiaryCode")),
             "company": {"partnerCompany": pp._bn(contrato), "unit": pp._bn((holder.get("company") or {}).get("unit"), str),
                         "requesterName": quem.get("name") or "", "requesterEmail": quem.get("email") or ""},
             "holderPartnerCompany": pp._bn(contrato), "inclusionType": "N"}
    return pp._limpo(corpo)


def incluir(contrato: str, pedido: str, novo: dict, executar: bool = False) -> dict:
    cpf = _cpf(novo.get("cpf"))
    with coleta_portal.sessao(contrato) as (page, h):
        def get(url):
            r = page.request.get(url, headers=h, timeout=90000)
            if not r.ok:
                raise RuntimeError(f"Hapvida respondeu {r.status} em {url.rsplit('/', 2)[-2:]}: {r.text()[:200]}")
            return r.json().get("content") or {}
        holder = get(f"{LS}/{pedido}")
        status = str((holder.get("movementStatus") or {}).get("code") or "")
        if status != "1":
            return {"ok": False, "status": status, "erro": f"pedido {pedido} não está Pendente "
                                                           f"({(holder.get('movementStatus') or {}).get('description') or status})"}
        deps = get(f"{LS}/{pedido}/dependents")
        if any(_cpf(d.get("document")) == cpf for d in deps.get("dependents") or []):
            return {"ok": True, "ja_no_pedido": True, "pedido": pedido}
        lista = perguntas_de(get(f"{API}/movements/forms/partner-company/{contrato}/health-declaration"
                                 f"?holderDocument={_cpf((holder.get('holder') or {}).get('document'))}"))
        falta = validar(novo, lista)
        if falta:
            return {"ok": False, "falta": falta, "erro": f"falta: {', '.join(falta)}"}
        corpo = montar(deps, holder, novo, contrato, pp._quem(page))
        res = {"pedido": pedido, "status": status, "payload": corpo}
        if not executar:
            return res
        page.request.get(f"{LS}/{pedido}/edition-lock", headers=h, timeout=60000)
        try:
            r = page.request.put(f"{LS}/{pedido}/dependents/pendencies", headers={**h, "content-type": "application/json"},
                                 data=json.dumps(corpo), timeout=120000)
            res["http"], res["resposta"] = r.status, r.text()[:800]
        finally:
            page.request.delete(f"{LS}/{pedido}/edition-lock", headers=h, timeout=60000)
        if res["http"] >= 300:
            res.update(ok=False, erro=f"a Hapvida recusou ({res['http']}): {res['resposta'][:300]}")
            return res
        de_novo = get(f"{LS}/{pedido}/dependents")
        v = next((d for d in de_novo.get("dependents") or [] if _cpf(d.get("document")) == cpf), None)
        st = get(f"{LS}/{pedido}/status")
        res.update(ok=bool(v), controle=(v or {}).get("controlNumber") or (v or {}).get("code"),
                   situacao=(st.get("status") or {}).get("description"), situacao_codigo=str((st.get("status") or {}).get("code") or ""))
        if not v:
            res["erro"] = "a Hapvida respondeu ok, mas a dependente não apareceu no pedido"
        return res


if __name__ == "__main__":
    if sys.argv[1] == "perguntas":
        print(json.dumps(perguntas(sys.argv[2].upper(), sys.argv[3]), ensure_ascii=False, indent=1))
