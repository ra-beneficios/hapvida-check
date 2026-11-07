"""07/11/2026 (André, MOV-000004 — Almir 68953885): o que a Hapvida diz HOJE do pedido de cada vida "Na operadora".

Antes o sistema só olhava os documentos exigidos da tela (os 3 estavam anexados) e não lia a PENDÊNCIA do pedido: a
Hapvida tinha registrado "710-PENDENCIA DE ESOCIAL OU CTPS DIGITAL" em 30/10 e ninguém viu por 8 dias.

- situacao(): só leitura. Lista de pedidos do contrato (status, pendência, observação) + críticas e documentos do
  pedido. Quem chama (lifetools, movimentacoes.operadora) segura a vez no portal (LOCK_PORTAL); aqui não pede de novo.
- anexar(): sobe um documento num espaço que a Hapvida ABRIU no pedido (required-documents) e confirma. A tela da
  Hapvida só deixa anexar nesses espaços (lido no código da tela em 07/11: getRequiredDocuments/uploadDocumentData/
  confirmDocuments); pendência sem espaço (ex.: vínculo eSocial/CTPS) vai por e-mail da cora@ — decisão do lifetools.
  Ver reference_portal_hapvida_anexar_documentos: tipo = description exata; nome do arquivo sem acento (400 senão).
"""
import re
import unicodedata
from datetime import date, timedelta

import httpx

import censo_saude
import exclusao_api

API = exclusao_api.API
LS = f"{API}/movements/life-screen/control-number"


def _sessao(contrato: str) -> dict:
    auth = exclusao_api._login(contrato)
    censo_saude.criar_sessao_empresa(auth, contrato)      # sem isso a lista responde 400 "selecione o contrato"
    return censo_saude._headers_http(auth)


def _get(h: dict, url: str, **params) -> dict:
    for _ in range(3):                                    # a Hapvida às vezes devolve 400/5xx e responde no minuto seguinte
        r = httpx.get(url, headers=h, params=params or None, timeout=90)
        if r.is_success:
            return r.json().get("content") or {}
    raise RuntimeError(f"Hapvida respondeu {r.status_code} em {url.rsplit('/', 2)[-2:]}: {r.text[:200]}")


def _pedidos(h: dict, contrato: str, dias: int = 179) -> list[dict]:
    fim = date.today()
    ini = fim - timedelta(days=dias)
    out, off = [], 0
    while True:
        c = _get(h, f"{API}/movements/status/partner-company/{contrato}/operation-types",
                 **{"start-date": ini.isoformat(), "end-date": fim.isoformat(), "offset": off, "limit": 100})
        lote = c.get("movements") or []
        out += lote
        off += len(lote)
        if not lote or off >= int(c.get("total") or 0):
            return out


def _data(s: str | None) -> tuple:
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", s or "")
    return (m.group(3), m.group(2), m.group(1)) if m else ("", "", "")


def _docs(h: dict, cn: str) -> list[dict]:
    c = _get(h, f"{LS}/{cn}/required-documents")
    return c.get("requiredDocs") or []


def situacao(contrato: str, cpfs: list[str]) -> dict:
    """-> {cpf: {...}} só para os CPFs pedidos. Sem pedido de inclusão do CPF nos últimos 180 dias: nao_achou."""
    cpfs = {re.sub(r"\D", "", c).zfill(11) for c in cpfs if c}
    h = _sessao(contrato)
    por_cpf: dict[str, dict] = {}
    for m in _pedidos(h, contrato):
        cpf = str(m.get("cpfNumber") or "").zfill(11)
        if cpf not in cpfs or m.get("operationDescription") != "Inclusion":
            continue
        atual = por_cpf.get(cpf)          # o pedido de inclusão mais recente do CPF
        chave = (_data(m.get("typingDate")), int(m.get("controlNumber") or 0))
        if not atual or chave > atual["_chave"]:
            por_cpf[cpf] = dict(m, _chave=chave)
    out = {}
    for cpf in cpfs:
        m = por_cpf.get(cpf)
        if not m:
            out[cpf] = {"nao_achou": True}
            continue
        cn = str(m["controlNumber"])
        r = {"pedido": cn, "dependente": str(m.get("dependentControlNumber") or "0") != "0",
             "situacao": m.get("processingStatusDescription"), "situacao_codigo": str(m.get("processingStatus") or ""),
             "situacao_em": m.get("statusDate"), "digitado_em": m.get("typingDate"), "vigencia": m.get("startDate"),
             "pendencia": (m.get("pendingDescription") or "").strip() or None,
             "observacao": (m.get("observation") or "").strip() or None, "criticas": [], "documentos": []}
        if r["situacao_codigo"] not in ("99",):           # ativo: nada mais a ler
            try:
                iss = _get(h, f"{LS}/{cn}/issues").get("beneficiaries") or []
                r["criticas"] = sorted({i.get("message") for b in iss for i in (b.get("health") or {}).get("issues") or []
                                        if i.get("message")})
                for d in _docs(h, cn):
                    if re.sub(r"\D", "", str(d.get("document") or "")).zfill(11) != cpf:
                        continue
                    r["documentos"] = [{"tipo": x.get("description"), "obrigatorio": bool(x.get("required")),
                                        "anexado": bool(x.get("fileName")), "anexado_em": x.get("uploadDate"),
                                        "aceita": x.get("groupDocuments") or []} for x in d.get("documents") or []]
            except Exception as e:                        # críticas/documentos são complemento: a situação já vale
                r["erro_detalhe"] = f"{type(e).__name__}: {e}"[:200]
        out[cpf] = r
    return out


def _sem_acento(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9._ -]", "_", s).strip() or "documento"


def anexar(contrato: str, pedido: str, cpf: str, tipo: str, nome: str, mime: str, conteudo: bytes) -> dict:
    """Sobe o arquivo no espaço `tipo` do pedido (titular) e confirma todos os documentos do pedido.
    -> {"ok": bool, "faltam": [...], "erro": str?}"""
    cpf = re.sub(r"\D", "", cpf).zfill(11)
    h = _sessao(contrato)
    docs = [d for d in _docs(h, pedido) if re.sub(r"\D", "", str(d.get("document") or "")).zfill(11) == cpf]
    if not docs:
        return {"ok": False, "erro": f"o pedido {pedido} não tem documentos para o CPF informado"}
    espacos = [x.get("description") for x in docs[0].get("documents") or []]
    if tipo not in espacos:
        return {"ok": False, "erro": f"o pedido {pedido} não tem o espaço '{tipo}' (tem: {', '.join(filter(None, espacos))})"}
    if str(docs[0].get("type") or "").lower() not in ("titular", "holder"):
        return {"ok": False, "erro": "anexo de dependente pelo robô ainda não mapeado"}
    arq = _sem_acento(nome)
    up = httpx.post(f"{LS}/{pedido}/document", headers={k: v for k, v in h.items() if k != "Accept"}, timeout=120,
                    files={"file": (arq, conteudo, mime)},
                    data={"holderControlNumber": pedido, "type": tipo, "holderDocumentNumber": cpf})
    if not up.is_success:
        return {"ok": False, "erro": f"upload respondeu {up.status_code}: {up.text[:200]}"}
    # confirma com TODOS os anexos do pedido (os de antes + o novo), como a tela faz
    docs = [d for d in _docs(h, pedido) if re.sub(r"\D", "", str(d.get("document") or "")).zfill(11) == cpf]
    anexos = [{"documentType": x["description"], "fileName": x["fileName"], "name": x.get("name") or x["fileName"],
               "path": x.get("path") or "", "type": x.get("type") or ""}
              for x in docs[0].get("documents") or [] if x.get("fileName")]
    conf = httpx.post(f"{LS}/{pedido}/required-documents", headers=h, timeout=90,
                      json={"holder": {"controlCode": int(pedido), "attachments": anexos}, "dependents": []})
    faltam = [x["description"] for x in docs[0].get("documents") or [] if x.get("required") and not x.get("fileName")]
    if not conf.is_success:
        return {"ok": False, "anexado": True, "faltam": faltam,
                "erro": f"anexou, mas a confirmação respondeu {conf.status_code}: {conf.text[:200]}"}
    return {"ok": True, "faltam": faltam, "arquivo": arq}
