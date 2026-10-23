"""Pedidos via tela parados em "Em digitação" (18/10/2026, André, 8DAC9 1332747).

Alguém começa a inclusão pela tela do portal e não clica em "Enviar": o pedido fica em "Em digitação" (status 22) e
nada acontece — não inclui nem pendencia. Regra do André: o que não inclui direto tem que pendenciar (é a prova para a
Hapvida resolver de imediato). Então o sistema conclui sozinho, exatamente como o botão "Enviar" da revisão:

  GET  life-screen/control-number/{cn}/edition-lock   (a tela abre a trava ao entrar no pedido)
  POST life-screen/control-number/{cn}/finish-typing  (o "Enviar"; 204) -> status passa a "Digitada" (0)

Não conclui (vai pro relatório):
  - pedido mexido hoje (alguém pode estar digitando agora);
  - alguém do pedido já ATIVO no mesmo contrato (regra do André: ativo no mesmo contrato não sobe).
Com crítica da tela tenta também. Se o portal não deixa sair de "Em digitação" (documento obrigatório sem anexo,
documento recusado, CEP inválido...), o resumo diz o que falta — isso é do pós-vendas.
1ª rodada (18/10/2026): 17 pedidos em 9 contratos; 14 saíram, 1 ativo no mesmo contrato, 2 precisam de documento.

  python3 pedidos_digitacao.py [--executar] [--avisar] (--todos | CONTRATO...)   (sem --executar: só mostra)
A sessão do portal fica presa ao contrato do login (outro contrato -> 400): um login por contrato.
"""
import json
import sys
import time
from datetime import date, datetime, timedelta

sys.path[:0] = ["/app", "/lifetools_service"]
from saneamento import coleta_portal  # noqa: E402

API = coleta_portal.API
LS = f"{API}/movements/life-screen/control-number"
EM_DIGITACAO = "22"


def _ativo_no_contrato(page, h, contrato: str, cpf: str) -> bool:
    if len(cpf.strip("0")) < 5:
        return False
    r = page.request.get(f"{API}/companies/partner-companies/{contrato}/beneficiaries?offset=0&limit=20&search={cpf}", headers=h)
    if not r.ok:
        raise RuntimeError(f"consulta de cadastro respondeu {r.status}")
    for it in ((r.json().get("content") or {}).get("beneficiaries")) or []:
        b = it.get("beneficiary") or {}
        st = str(b.get("status") or "").lower()
        if (b.get("user") or {}).get("document") == cpf and not any(x in st for x in ("cancel", "inativ", "exclu")):
            return True
    return False


def pedidos(page, h, contrato: str, dias: int = 60) -> dict[str, list[dict]]:
    """controlNumber do titular -> vidas do pedido em digitação."""
    fim = datetime.now()
    ini = (fim - timedelta(days=dias)).strftime("%Y-%m-%d")
    out, off = {}, 0
    while True:
        r = page.request.get(f"{API}/movements/status/partner-company/{contrato}/operation-types?start-date={ini}"
                             f"&end-date={fim:%Y-%m-%d}&offset={off}&limit=100", headers=h, timeout=90000)
        if not r.ok:
            raise RuntimeError(f"lista de pedidos respondeu {r.status}")
        c = r.json().get("content") or {}
        for p in c.get("movements") or []:
            if str(p.get("processingStatus")) == EM_DIGITACAO:
                out.setdefault(str(p["controlNumber"]), []).append(
                    {"nome": p.get("name"), "cpf": (p.get("cpfNumber") or "").zfill(11), "status_em": p.get("statusDate")})
        off += 100
        if not c.get("movements") or off >= int(c.get("total") or 0):
            return out


def _status(page, h, cn: str) -> dict:
    return page.request.get(f"{LS}/{cn}/status", headers=h).json().get("content") or {}


def concluir(page, h, cn: str, espera_s: int = 180) -> dict:
    page.request.get(f"{LS}/{cn}/edition-lock", headers=h, timeout=60000)
    r = page.request.post(f"{LS}/{cn}/finish-typing", headers=h, timeout=120000)
    if r.status >= 300:
        return {"ok": False, "erro": f"finish-typing {r.status}: {r.text()[:200]}"}
    fim = time.time() + espera_s
    st = _status(page, h, cn)
    while str((st.get("status") or {}).get("code")) == EM_DIGITACAO and time.time() < fim:
        time.sleep(15)
        st = _status(page, h, cn)
    ok = str((st.get("status") or {}).get("code")) != EM_DIGITACAO
    return {"ok": ok, "status": (st.get("status") or {}).get("description"), **({} if ok else {"falta": o_que_trava(page, h, cn)})}


def o_que_trava(page, h, cn: str) -> list[str]:
    """O que o portal mostra quando não deixa concluir: críticas, documentos recusados e obrigatórios sem anexo."""
    out = []
    iss = (page.request.get(f"{LS}/{cn}/issues", headers=h).json().get("content") or {}).get("beneficiaries") or []
    for b in iss:
        for i in (b.get("health") or {}).get("issues") or []:
            out.append(f"{b.get('name')}: {i.get('message')}")
        for i in (b.get("health") or {}).get("documentIssues") or []:
            out.append(f"{b.get('name')}: {i.get('groupDescription') or i.get('message')}")
    docs = (page.request.get(f"{LS}/{cn}/required-documents", headers=h).json().get("content") or {}).get("requiredDocs") or []
    for d in docs:
        falt = [x["description"] for x in d.get("documents") or [] if x.get("required")]
        if falt:
            out.append(f"{d.get('name')}: anexar " + ", ".join(falt))
    return out


def contratos_saude() -> list[str]:
    """Contratos Hapvida Saúde ativos no cadastro. 23/10/2026 (André): antes só os com fatura nos últimos 60 dias, e
    contrato novo (G5LXS) e mais 6 ficavam de fora."""
    import ciclo_criticas
    conn = ciclo_criticas._conn_leitura()
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT codigo_externo FROM empresa_operadora_codigos
                            WHERE operadora_id = 1 AND modalidade = 'SAUDE' AND situacao = 'ATIVO' ORDER BY 1""")
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def resumo(todos: list[dict]) -> str:
    saiu = [x for x in todos if x.get("acao") == "CONCLUIDO" and x.get("ok")]
    trava = [x for x in todos if x.get("acao") == "CONCLUIDO" and not x.get("ok")]
    ativo = [x for x in todos if x.get("acao") == "NAO_CONCLUIR"]
    erro = [x for x in todos if x.get("acao") == "ERRO"]
    if not (saiu or trava or ativo or erro):
        return ""
    p = ["🖊️ **Pedidos via tela parados em \"Em digitação\"**"]
    if saiu:
        p.append(f"✅ {len(saiu)} concluído(s) pelo sistema: " + "; ".join(f"{x['contrato']} {x['pedido']} → {x['status']}" for x in saiu))
    for x in trava:
        p.append(f"⏳ {x['contrato']} {x['pedido']} ({', '.join(x['vidas'])}) precisa do pós-vendas: " + " | ".join(x.get("falta") or ["?"]))
    for x in ativo:
        p.append(f"⏸️ {x['contrato']} {x['pedido']}: {x['motivo']} — não concluí (cancelar o pedido no portal)")
    for x in erro:
        p.append(f"⚠️ {x['contrato']}: {x['motivo']}")
    return "\n".join(p)


def tratar_contrato(contrato: str, executar: bool) -> list[dict]:
    hoje = date.today().strftime("%d/%m/%Y")
    res = []
    with coleta_portal.sessao(contrato) as (page, h):
        for cn, vidas in pedidos(page, h, contrato).items():
            item = {"contrato": contrato, "pedido": cn, "vidas": [v["nome"] for v in vidas]}
            if any(v["status_em"] == hoje for v in vidas):
                res.append({**item, "acao": "AGUARDAR", "motivo": "mexido hoje — alguém pode estar digitando"})
                continue
            ativos = [v["nome"] for v in vidas if _ativo_no_contrato(page, h, contrato, v["cpf"])]
            if ativos:
                res.append({**item, "acao": "NAO_CONCLUIR", "motivo": "já ativo(s) no mesmo contrato: " + ", ".join(ativos)})
                continue
            criticas = len(((page.request.get(f"{LS}/{cn}/issues", headers=h).json().get("content")) or {}).get("beneficiaries") or [])
            if not executar:
                res.append({**item, "acao": "CONCLUIRIA", "criticas": criticas})
                continue
            res.append({**item, "acao": "CONCLUIDO", "criticas": criticas, **concluir(page, h, cn)})
    return res


if __name__ == "__main__":
    args = sys.argv[1:]
    executar = "--executar" in args
    contratos = contratos_saude() if "--todos" in args else [a for a in args if not a.startswith("--")]
    todos = []
    for ct in contratos:
        try:
            r = tratar_contrato(ct.upper(), executar)
        except Exception as e:
            if "nao encontrado na busca" in str(e):   # código que o login da Cora não enxerga (9WLZU, ERQ43, ICBJY, Z8TUR)
                print(f"{ct}: fora do login do portal - ignorado", flush=True)
                continue
            r = [{"contrato": ct, "acao": "ERRO", "motivo": f"{type(e).__name__}: {str(e)[:200]}"}]
        for x in r:
            print(json.dumps(x, ensure_ascii=False), flush=True)
        todos += r
    texto = resumo(todos)
    if texto and "--avisar" in args:
        import ciclo_criticas
        ciclo_criticas._cora("/notificar", {"upn": ciclo_criticas.ciclo.ADMIN, "texto": texto})
