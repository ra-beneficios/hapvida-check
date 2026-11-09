"""Corrigir um dado de dependente num pedido PENDENTE da Hapvida, como o "Tratar pendência" da tela (09/11/2026).

André (MOV-14 l.117 Angelo, pedido 69928354): "cadê o campo de alterar a data de nascimento? ... troca e envia para
validação". O pedido já está na Hapvida (Pendente, pedindo documento por "data de nascimento diferente"); reenviar o TXT
criaria outro pedido. A tela da Hapvida edita o pedido com:

  GET  life-screen/control-number/{cn}/edition-lock           (a tela abre a trava ao entrar)
  GET  life-screen/control-number/{cn}/dependents             (dados do pedido)
  PUT  life-screen/control-number/{cn}/dependents/pendencies  (o "Salvar" do modo with-pendency)
  DELETE life-screen/control-number/{cn}/edition-lock

O corpo do PUT é o mesmo que o JS da tela monta (main.229a5ef7.js, funções de mapeamento do formulário de dependentes):
GET -> formulário -> payload. Só a vida corrigida vai como "Updated" (2); as outras vão "NotChanged" (0), iguais.
Depois do PUT o pedido é lido de novo e a data é conferida.

    python3 pedido_pendencia.py CONTRATO PEDIDO CPF DD/MM/AAAA            (só mostra o payload)
    python3 pedido_pendencia.py CONTRATO PEDIDO CPF DD/MM/AAAA --executar
"""
import json
import re
import sys

sys.path[:0] = ["/app", "/lifetools_service"]
from saneamento import coleta_portal  # noqa: E402

API = coleta_portal.API
LS = f"{API}/movements/life-screen/control-number"
NOT_CHANGED, UPDATED = 0, 2
PRIMARY_MEMBER_INCLUSION = "4"      # OI.PRIMARY_MEMBER_INCLUSION: inclusão junto do titular (não "contrato ativo")


def _data_iso(ddmmaaaa: str | None) -> str | None:
    """VN() da tela: DD/MM/AAAA -> AAAA-MM-DDT00:00:00Z."""
    if not ddmmaaaa:
        return None
    d, m, a = ddmmaaaa.split("/")
    return f"{a}-{m.zfill(2)}-{d.zfill(2)}T00:00:00Z"


def _ddmmaaaa(iso: str | None) -> str:
    """FN() da tela: AAAA-MM-DDT... -> DD/MM/AAAA."""
    if not iso:
        return ""
    a, m, d = iso.split("T")[0].split("-")
    return f"{d.zfill(2)}/{m.zfill(2)}/{a}"


def _limpo(v):
    """YN() da tela: tira None e objeto vazio, recursivo."""
    if isinstance(v, list):
        return [x for x in (_limpo(i) for i in v) if x is not None]
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            r = _limpo(x)
            if r is None or (isinstance(r, dict) and not r):
                continue
            out[k] = r
        return out
    return v


def _bn(v, f=str):
    """BN() da tela: valor falso (vazio, 0) some."""
    return f(v) if v else None


def _dependente(dep: dict, holder_code: str, holder_name: str, dental_company: str | None, op: int) -> dict:
    """GET -> formulário (mapeamento da tela) -> item do payload."""
    pw = dep.get("payWaitingPeriod") or {}
    plano = dep.get("plan") or {}
    dental = plano.get("dentalPlan") or {}
    contato = dep.get("contact") or {}
    decl = [{"questionNumber": str(q["questionNumber"]), "response": bool(q.get("response")),
             "description": q.get("description") or ""} for q in dep.get("healthDeclarations") or [] if q.get("questionNumber")]
    doc = re.sub(r"\D", "", str(dep.get("document") or "")).zfill(11) if dep.get("document") else None
    return {
        "code": _bn(dep.get("code")), "operationType": op, "sequentialNumber": _bn(dep.get("sequentialNumber"), int),
        "name": _bn(dep.get("name")), "birthDate": _bn(_data_iso(_ddmmaaaa(dep.get("birthDate")))),
        "document": doc, "gender": _bn(dep.get("gender")), "civilStatus": _bn(dep.get("civilStatus"), int),
        "mothersName": _bn(dep.get("mothersName")), "holderCode": _bn(holder_code), "holderName": _bn(holder_name),
        "marriageDate": _bn(_data_iso(_ddmmaaaa(dep.get("marriageDate")))),
        "beneficiaryType": _bn((dep.get("beneficiaryType") or {}).get("beneficiaryTypeCode"), int),
        "pis": _bn(dep.get("pis")), "ctps": _bn(dep.get("ctps")), "ctpsSeries": _bn(dep.get("ctpsSeries")),
        "cnsCode": _bn(dep.get("cnsCode")), "companyRegistration": _bn(dep.get("companyRegistration")),
        "registrationSequence": _bn(dep.get("registrationSequence")),
        "liveBirthDeclaration": _bn(dep.get("liveBirthDeclaration")),
        "contact": {"cellNumber": _bn(re.sub(r"\D", "", str(contato.get("cellNumber") or ""))), "email": _bn(contato.get("email"))},
        "healthDeclarations": decl or None,
        "plan": {"startDate": _bn(_data_iso(_ddmmaaaa(plano.get("startDate")))),
                 "health": {"code": _bn((plano.get("healthPlan") or {}).get("healthPlanCode"))},
                 "dental": {"code": _bn(dental.get("dentalCode")), "companyCode": _bn(dental_company)} if dental.get("dentalCode") else None,
                 "startDateType": PRIMARY_MEMBER_INCLUSION},
        "payWaitingPeriod": {"waitingPeriod": _bn(_data_iso(_ddmmaaaa(pw.get("waitingPeriod")))),
                             "beneficiaryCode": _bn(pw.get("beneficiaryCode")),
                             "healthInsuranceCode": _bn(pw.get("healthInsuranceCode")),
                             "startDate": _bn(_data_iso(_ddmmaaaa(pw.get("startDate")))),
                             "endDate": _bn(_data_iso(_ddmmaaaa(pw.get("endDate")))),
                             "segmentation": _bn((pw.get("segmentation") or {}).get("segmentationCode"), int),
                             "accommodation": _bn((pw.get("accommodation") or {}).get("accommodationCode"), int)},
        "ocrDocuments": dep.get("ocrDocuments") or None,
    }


def montar(deps: dict, holder: dict, cpf: str, nascimento: str, quem: dict) -> dict:
    """Payload do PUT dependents/pendencies com a data de nascimento de `cpf` trocada."""
    hc, hn = str(deps.get("holderCode") or ""), deps.get("holderName") or ""
    h = holder.get("holder") or holder
    dental_company = ((h.get("plan") or {}).get("dentalPlan") or {}).get("dentalCompanyCode") or deps.get("dentalCompanyCode")
    itens, achou = [], False
    for d in deps.get("dependents") or []:
        alvo = re.sub(r"\D", "", str(d.get("document") or "")).zfill(11) == cpf
        if alvo:
            d = dict(d, birthDate=_data_iso(nascimento))
            achou = True
        itens.append(_dependente(d, hc, hn, dental_company, UPDATED if alvo else NOT_CHANGED))
    if not achou:
        raise ValueError(f"CPF {cpf} não é dependente do pedido {hc}")
    corpo = {"dependents": itens, "holderCode": _bn(hc),
             "holderDocument": _bn(re.sub(r"\D", "", str(deps.get("holderDocument") or "")).zfill(11)),
             "beneficiaryCode": _bn(h.get("beneficiaryCode") or deps.get("holderBeneficiaryCode")),
             "company": {"partnerCompany": _bn(deps.get("holderPartnerCompany")),
                         "unit": _bn(deps.get("holderUnitCompany")),
                         "requesterName": quem.get("name") or "", "requesterEmail": quem.get("email") or ""},
             "holderPartnerCompany": _bn(deps.get("holderPartnerCompany")), "inclusionType": "N"}
    return _limpo(corpo)


def _quem(page) -> dict:
    try:
        bruto = page.evaluate("() => localStorage.getItem('@hapvida/account-data')") or "{}"
        d = json.loads(bruto)
        u = d.get("user") or d
        return {"name": u.get("name") or u.get("nome") or "", "email": u.get("email") or ""}
    except Exception:
        return {"name": "", "email": ""}


def corrigir_nascimento(contrato: str, pedido: str, cpf: str, nascimento: str, executar: bool = False) -> dict:
    cpf = re.sub(r"\D", "", cpf).zfill(11)
    with coleta_portal.sessao(contrato) as (page, h):
        def get(url):
            r = page.request.get(url, headers=h, timeout=90000)
            if not r.ok:
                raise RuntimeError(f"Hapvida respondeu {r.status} em {url.rsplit('/', 2)[-2:]}: {r.text()[:200]}")
            return r.json().get("content") or {}
        deps = get(f"{LS}/{pedido}/dependents")
        holder = get(f"{LS}/{pedido}")
        antes = next((d for d in deps.get("dependents") or []
                      if re.sub(r"\D", "", str(d.get("document") or "")).zfill(11) == cpf), {})
        corpo = montar(deps, holder, cpf, nascimento, _quem(page))
        res = {"pedido": pedido, "cpf": cpf, "status": deps.get("holderMovementStatusDescription"),
               "antes": _ddmmaaaa(antes.get("birthDate")), "pedido_para": nascimento, "payload": corpo}
        if not executar:
            return res
        if str(deps.get("holderMovementStatusCode")) != "1":
            raise RuntimeError(f"pedido {pedido} não está Pendente ({deps.get('holderMovementStatusDescription')})")
        page.request.get(f"{LS}/{pedido}/edition-lock", headers=h, timeout=60000)
        try:
            r = page.request.put(f"{LS}/{pedido}/dependents/pendencies", headers={**h, "content-type": "application/json"},
                                 data=json.dumps(corpo), timeout=120000)
            res["http"] = r.status
            res["resposta"] = r.text()[:1500]
        finally:
            page.request.delete(f"{LS}/{pedido}/edition-lock", headers=h, timeout=60000)
        depois = get(f"{LS}/{pedido}/dependents")
        d2 = next((d for d in depois.get("dependents") or []
                   if re.sub(r"\D", "", str(d.get("document") or "")).zfill(11) == cpf), {})
        res["depois"] = _ddmmaaaa(d2.get("birthDate"))
        res["status_depois"] = depois.get("holderMovementStatusDescription")
        res["ok"] = res["depois"] == nascimento
        return res


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    r = corrigir_nascimento(a[0].upper(), a[1], a[2], a[3], executar="--executar" in sys.argv)
    print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
