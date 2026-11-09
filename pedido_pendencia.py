"""Corrigir um dado de dependente num pedido PENDENTE da Hapvida, como o "Tratar pendência" da tela (09/11/2026).

André (MOV-14 l.117 Angelo, pedido 69928354): "cadê o campo de alterar a data de nascimento? ... troca e envia para
validação". O pedido já está na Hapvida (Pendente, pedindo documento por "data de nascimento diferente"); reenviar o TXT
criaria outro pedido. A tela da Hapvida edita o pedido com:

  GET  life-screen/control-number/{cn}/edition-lock           (a tela abre a trava ao entrar)
  GET  life-screen/control-number/{cn}/dependents             (dados do pedido)
  PUT  life-screen/control-number/{cn}/dependents/pendencies  (o "Salvar" do modo with-pendency, dependente)
  PUT  life-screen/control-number/{cn}/pendencies             (o mesmo, titular: GET life-screen/control-number/{cn})
  DELETE life-screen/control-number/{cn}/edition-lock
  POST life-screen/control-number/{cn}/finish-typing          (o "Enviar" da revisão: devolve à validação)

Titular: no modo pendência a tela obriga escolher a data de início do plano (plan-start-date/.../pending-issues:
originalStartDate, cutOffDate, startDate). Vai a ORIGINAL (a vigência que o cliente pediu no arquivo).
Qual data usar (decidir_data): a digitada pelo pós-vendas; senão a do cadastro da própria Hapvida (censo, regra do
André: "vale a da Hapvida"); senão a da Receita (life-data) quando ela difere do pedido. Sem nenhuma: documento.
1ª vez (09/11/2026): Angelo 69928354 e Helloá 69928256 (4DFEV) -> Processado na hora.

O corpo do PUT é o mesmo que o JS da tela monta (main.229a5ef7.js, funções de mapeamento do formulário de dependentes):
GET -> formulário -> payload. Só a vida corrigida vai como "Updated" (2); as outras vão "NotChanged" (0), iguais.
Depois do PUT o pedido é lido de novo e a data é conferida.

    python3 pedido_pendencia.py CONTRATO PEDIDO CPF DD/MM/AAAA            (só mostra o payload)
    python3 pedido_pendencia.py CONTRATO PEDIDO CPF DD/MM/AAAA --executar
"""
import json
import re
import sys
import time

sys.path[:0] = ["/app", "/lifetools_service"]
from saneamento import coleta_portal  # noqa: E402

API = coleta_portal.API
LS = f"{API}/movements/life-screen/control-number"
NOT_CHANGED, UPDATED = 0, 2
PRIMARY_MEMBER_INCLUSION = "4"      # OI.PRIMARY_MEMBER_INCLUSION: inclusão junto do titular (não "contrato ativo")
ORIGINAL_DATE = "1"                 # OI.ORIGINAL_DATE


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


def montar_titular(dados: dict, contrato: str, nascimento: str, inicio: dict, quem: dict) -> dict:
    """Payload do PUT pendencies (titular), como o formulário do titular da tela monta."""
    h = dados.get("holder") or {}
    pw, plano, end = h.get("payWaitingPeriod") or {}, h.get("plan") or {}, h.get("address") or {}
    dental, contato, cons = plano.get("dentalPlan") or {}, h.get("contact") or {}, h.get("consigned") or {}
    reemb, ident = h.get("reimbursement") or {}, h.get("identityCard") or {}
    decl = [{"questionNumber": str(q["questionNumber"]), "response": bool(q.get("response")),
             "description": q.get("description") or ""} for q in h.get("healthDeclarations") or [] if q.get("questionNumber")]
    inicio_data = inicio.get("originalStartDate") or _ddmmaaaa(plano.get("startDate"))
    corpo = {
        "controlNumber": _bn(dados.get("controlNumber"), int),
        "company": {"partnerCompany": _bn(contrato), "unit": _bn((dados.get("company") or {}).get("unit")),
                    "requesterName": quem.get("name") or "", "requesterEmail": quem.get("email") or ""},
        "holder": {
            "name": _bn(h.get("name")), "birthDate": _bn(_data_iso(nascimento)),
            "document": _bn(re.sub(r"\D", "", str(h.get("document") or "")).zfill(11)), "gender": _bn(h.get("gender")),
            "civilStatus": _bn(h.get("civilStatus"), int), "mothersName": _bn(h.get("mothersName")),
            "pis": _bn(h.get("pis")), "ctps": _bn(h.get("ctps")), "ctpsSeries": _bn(h.get("ctpsSeries")),
            "cnsCode": _bn(h.get("cnsCode")), "companyRegistration": _bn(h.get("companyRegistration")),
            "registrationSequence": _bn(h.get("registrationSequence")),
            "liveBirthDeclaration": _bn(h.get("liveBirthDeclaration")),
            "identityCard": _bn(ident.get("number") if isinstance(ident, dict) else ident),
            "identityCardIssuerCode": _bn(ident.get("issuerCode") if isinstance(ident, dict) else h.get("identityCardIssuerCode")),
            "identityCardUf": _bn(ident.get("uf") if isinstance(ident, dict) else h.get("identityCardUf")),
            "admissionAt": _bn(_data_iso(_ddmmaaaa(h.get("admissionAt")))),
            "occupation": _bn((h.get("occupation") or {}).get("occupationCode") if isinstance(h.get("occupation"), dict) else h.get("occupation"), int),
            "contact": {"phoneNumber": _bn(re.sub(r"\D", "", str(contato.get("phoneNumber") or ""))),
                        "cellNumber": _bn(re.sub(r"\D", "", str(contato.get("cellNumber") or ""))), "email": _bn(contato.get("email"))},
            "healthDeclarations": decl or None,
            "plan": {"startDate": _bn(_data_iso(inicio_data)),
                     "health": {"code": _bn((plano.get("healthPlan") or {}).get("healthPlanCode")),
                                "careLocation": _bn((plano.get("careLocation") or {}).get("careLocationCode"))},
                     "dental": {"code": _bn(dental.get("dentalCode")), "companyCode": _bn(dental.get("dentalCompanyCode"))},
                     "startDateType": ORIGINAL_DATE},
            "payWaitingPeriod": {"waitingPeriod": _bn(_data_iso(_ddmmaaaa(pw.get("waitingPeriod")))),
                                 "beneficiaryCode": _bn(pw.get("beneficiaryCode")),
                                 "healthInsuranceCode": _bn(pw.get("healthInsuranceCode")),
                                 "startDate": _bn(_data_iso(_ddmmaaaa(pw.get("startDate")))),
                                 "endDate": _bn(_data_iso(_ddmmaaaa(pw.get("endDate")))),
                                 "segmentation": _bn((pw.get("segmentation") or {}).get("segmentationCode"), int),
                                 "accommodation": _bn((pw.get("accommodation") or {}).get("accommodationCode"), int)},
            "address": {"zipCode": _bn(re.sub(r"\D", "", str(end.get("zipCode") or ""))), "typePublicPlace": _bn(end.get("typePublicPlace")),
                        "publicPlace": _bn(end.get("publicPlace")), "number": _bn(end.get("number")),
                        "complement": _bn(end.get("complement")), "district": _bn(end.get("district")),
                        "city": _bn(end.get("city")), "stateCode": _bn(end.get("stateCode")),
                        "referencePoint": _bn(end.get("referencePoint"))},
            "reimbursement": {"bankCode": _bn(reemb.get("bankCode")), "agency": _bn(reemb.get("agency")),
                              "account": _bn(reemb.get("account")), "digit": _bn(reemb.get("digit"))},
            "consigned": {"salary": _bn(cons.get("salary")), "billingType": _bn(cons.get("billingType")),
                          "contractNumber": _bn(cons.get("contractNumber")),
                          "isPensioner": cons.get("isPensioner") if isinstance(cons.get("isPensioner"), bool) else None,
                          "isEmployeeOnLeave": cons.get("isEmployeeOnLeave") if isinstance(cons.get("isEmployeeOnLeave"), bool) else None,
                          "hasSubsidy": cons.get("hasSubsidy") if isinstance(cons.get("hasSubsidy"), bool) else None,
                          "dailySubsidy": cons.get("dailySubsidy") if isinstance(cons.get("dailySubsidy"), bool) else None,
                          "beneficiaryClassification": _bn(cons.get("beneficiaryClassification")),
                          "pensionerRegistration": _bn(cons.get("pensionerRegistration")),
                          "complementaryRegistration": _bn(cons.get("complementaryRegistration")),
                          "payrollDeduction": _bn(cons.get("payrollDeduction")), "employmentLink": _bn(cons.get("employmentLink"))},
            "ocrDocuments": h.get("ocrDocuments") or None,
        }}
    return _limpo(corpo)


def _quem(page) -> dict:
    try:
        bruto = page.evaluate("() => localStorage.getItem('@hapvida/account-data')") or "{}"
        d = json.loads(bruto)
        u = d.get("user") or d
        return {"name": u.get("name") or u.get("nome") or "", "email": u.get("email") or ""}
    except Exception:
        return {"name": "", "email": ""}


def decidir_data(atual: str, digitada: str | None, hapvida: str | None, receita: str | None) -> tuple[str | None, str]:
    """-> (data nova, fonte). None = nenhuma fonte diz outra data (o caminho é o documento)."""
    for data, fonte in ((digitada, "pós-vendas"), (hapvida, "cadastro da Hapvida"), (receita, "Receita")):
        if data and re.fullmatch(r"\d{2}/\d{2}/\d{4}", data) and data != atual:
            return data, fonte
    return None, ""


def corrigir_nascimento(contrato: str, pedido: str, cpf: str, nascimento: str | None = None, executar: bool = False,
                        data_hapvida: str | None = None, enviar: bool = True) -> dict:
    """Troca a data de nascimento de `cpf` no pedido (titular ou dependente) e devolve à validação (finish-typing).
    Sem `nascimento`, decide pela data da Hapvida (data_hapvida, do censo) e pela Receita."""
    cpf = re.sub(r"\D", "", cpf).zfill(11)
    with coleta_portal.sessao(contrato) as (page, h):
        def get(url):
            r = page.request.get(url, headers=h, timeout=90000)
            if not r.ok:
                raise RuntimeError(f"Hapvida respondeu {r.status} em {url.rsplit('/', 2)[-2:]}: {r.text()[:200]}")
            return r.json().get("content") or {}

        def doc(x):
            return re.sub(r"\D", "", str(x.get("document") or "")).zfill(11)
        receita = None
        if not nascimento and not data_hapvida:
            # antes de ler o pedido: com pedido pendente a Receita vem por outro contrato (troca e volta a sessão)
            ld = coleta_portal.life_data(page, h, contrato, cpf) or {}
            receita = ld.get("birthDate") if not ld.get("_ja_ativo_em") else None
        holder = get(f"{LS}/{pedido}")
        titular = doc(holder.get("holder") or {}) == cpf
        deps = {} if titular else get(f"{LS}/{pedido}/dependents")
        vida = (holder.get("holder") or {}) if titular else next((d for d in deps.get("dependents") or [] if doc(d) == cpf), None)
        if not vida:
            raise ValueError(f"CPF {cpf} não está no pedido {pedido}")
        status = str((holder.get("movementStatus") or {}).get("code") or deps.get("holderMovementStatusCode") or "")
        atual = _ddmmaaaa(vida.get("birthDate"))
        nova, fonte = decidir_data(atual, nascimento, data_hapvida, receita)
        res = {"pedido": pedido, "cpf": cpf, "titular": titular, "status": status, "antes": atual, "nova": nova,
               "fonte": fonte, "receita": receita}
        if not nova:
            res["sem_fonte"] = True
            return res
        if titular:
            inicio = get(f"{API}/movements/forms/partner-company/{contrato}/plan-start-date/control-number/{pedido}/pending-issues")
            corpo, url = montar_titular(holder, contrato, nova, inicio, _quem(page)), f"{LS}/{pedido}/pendencies"
        else:
            corpo, url = montar(deps, holder, cpf, nova, _quem(page)), f"{LS}/{pedido}/dependents/pendencies"
        res["payload"] = corpo
        if not executar:
            return res
        if status != "1":
            raise RuntimeError(f"pedido {pedido} não está Pendente (situação {status})")
        page.request.get(f"{LS}/{pedido}/edition-lock", headers=h, timeout=60000)
        try:
            r = page.request.put(url, headers={**h, "content-type": "application/json"}, data=json.dumps(corpo), timeout=120000)
            res["http"], res["resposta"] = r.status, r.text()[:800]
        finally:
            page.request.delete(f"{LS}/{pedido}/edition-lock", headers=h, timeout=60000)
        if res["http"] >= 300:
            res["ok"] = False
            return res
        de_novo = get(f"{LS}/{pedido}") if titular else get(f"{LS}/{pedido}/dependents")
        v2 = (de_novo.get("holder") or {}) if titular else next((d for d in de_novo.get("dependents") or [] if doc(d) == cpf), {})
        res["depois"] = _ddmmaaaa(v2.get("birthDate"))
        res["ok"] = res["depois"] == nova
        if res["ok"] and enviar:
            page.request.get(f"{LS}/{pedido}/edition-lock", headers=h, timeout=60000)
            f = page.request.post(f"{LS}/{pedido}/finish-typing", headers=h, timeout=120000)
            res["enviado"] = f.status < 300
            fim = time.time() + 120
            st = {}
            while time.time() < fim:
                time.sleep(15)
                st = (page.request.get(f"{LS}/{pedido}/status", headers=h).json().get("content") or {}).get("status") or {}
                if str(st.get("code")) not in ("1", "22"):
                    break
            res["situacao"] = st.get("description")
            res["situacao_codigo"] = str(st.get("code") or "")
        return res


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    r = corrigir_nascimento(a[0].upper(), a[1], a[2], a[3] if len(a) > 3 else None, executar="--executar" in sys.argv)
    print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
