"""Regularizar onda de exclusão que subiu e ficou "não enviada" (01/11/2026, inspeção MOV A2/A22). Sem portal, sem banco
(cursor simulado), lote em pasta temporária com o motor de exclusão de verdade. Roda na imagem do hapvida-check."""
# Rodar: docker run --rm --network none --entrypoint python -e PYTHONPATH=/app -e MOTOR_EXCLUSAO_PATH=/motor -w /app <imagem> tests/teste_regularizar_envio.py
import datetime
import hashlib
import json
import os
import sys
import tempfile

TMP = tempfile.mkdtemp()
os.environ["EXCLUSAO_LOTES_DIR"] = TMP
sys.path[:0] = ["/app", "/motor"]
from motor_exclusao import servico  # noqa: E402
import regularizar_envio_exclusao as reg  # noqa: E402

falhas = 0


def ok(cond, msg):
    global falhas
    print(("ok    " if cond else "FALHOU ") + msg)
    falhas += 0 if cond else 1


PASTAS = servico.Pastas(TMP, TMP)
TXT = b"linha de exclusao ficticia\n"
SHA = hashlib.sha256(TXT).hexdigest()
DEP = {"onda": "dependentes", "contrato": "ZZTST", "arquivo": "EXCLUSAO_ZZTST_dependentes_x.txt", "hash": SHA, "linhas": 1}
TIT = {"onda": "titulares", "contrato": "ZZTST", "arquivo": "EXCLUSAO_ZZTST_titulares_y.txt", "hash": "t" * 64, "linhas": 1}
QUANDO = datetime.datetime(2026, 10, 31, 3, 17, tzinfo=datetime.timezone(datetime.timedelta(hours=-3)))


def lote(nome):
    os.makedirs(os.path.join(TMP, nome, "retorno"), exist_ok=True)
    open(os.path.join(TMP, nome, DEP["arquivo"]), "wb").write(TXT)
    servico._salvar_estado({"lote": nome, "solicitante_upn": "pos@ra", "aprovacoes": [{"arquivos": [TIT, DEP]}],
                            "envios": [{**TIT, "status": 200, "protocolo": "111"}]}, PASTAS)
    return nome


class Banco:
    """Responde às consultas da ferramenta e guarda o que ela grava."""

    def __init__(self, tem_prova=True):
        self.prova = (472, "1337619", "NO_PORTAL", QUANDO) if tem_prova else None
        self.acomp, self.notas, self.consertos, self._r, self.status = [], [], [], None, "AGUARDANDO_CHECAGEM_INICIAL"

    def execute(self, sql, args=None):
        s = " ".join(sql.split())
        if "FROM portal.envio" in s:
            self._r = self.prova if args[1] == SHA else None
        elif s.startswith("SELECT id, status, solicitante_nome, controle_portal FROM movimentacoes_hapvida_acompanhamento"):
            self._r = next(((99, self.status, a[7], a[2]) for a in self.acomp if a[4] == args[0]), None)
        elif s.startswith("UPDATE movimentacoes_hapvida_acompanhamento"):
            self.consertos.append(args)
            self.status = "AGUARDANDO_CHECAGEM_INICIAL"
        elif s.startswith("SELECT mov_chamado_id"):
            self._r = (6,) if args[0] == TIT["arquivo"] else None
        elif s.startswith("INSERT INTO movimentacoes_hapvida_acompanhamento"):
            self.acomp.append(args)
        elif "tipo = 'NOTA'" in s:
            self._r = (1,) if any(n[1] == args[1] for n in self.notas) else None

    def fetchone(self):
        return self._r

    def evento(self, cur, cid, tipo, detalhe):
        self.notas.append((cid, detalhe["regularizado"], tipo, detalhe))


# A. simulação não muda nada
nome = lote("A")
b = Banco()
passos = reg.regularizar(b, servico, PASTAS, nome, "dependentes", "andre@ra", "conferido no portal.envio 472", False, b.evento)
est = servico.ler_estado(nome, PASTAS)
ok(len(est["envios"]) == 1 and not b.acomp and not b.notas, "A: simulação não grava lote, acompanhamento nem nota")
ok(any("movimentação 1337619" in p for p in passos), "A: mostra a prova (movimentação do portal)")

# B. aplicar: lote, acompanhamento (com o chamado da onda dos titulares) e NOTA
passos = reg.regularizar(b, servico, PASTAS, nome, "dependentes", "andre@ra", "conferido no portal.envio 472", True, b.evento)
est = servico.ler_estado(nome, PASTAS)
dep = [e for e in est["envios"] if e["onda"] == "dependentes"]
ok(len(dep) == 1 and dep[0]["protocolo"] == "1337619" and dep[0]["regularizado"]["portal_envio"] == 472,
   "B: envio gravado no lote com a movimentação e a origem da regularização")
ok(len(b.acomp) == 1 and b.acomp[0][1] == "1337619" and b.acomp[0][8] == 6, "B: acompanhamento criado, ligado ao chamado 6")
ok(b.acomp[0][2] == "1337619" and b.acomp[0][3] == QUANDO,
   "B: com a movimentação (controle_portal) e o horário do envio — o vigia acha (23h: sem eles marcou FALHOU)")
ok(len(b.notas) == 1 and b.notas[0][0] == 6 and b.notas[0][2] == "NOTA" and b.notas[0][3]["autor"] == "andre@ra",
   "B: NOTA no chamado com autor")

# C. de novo: não duplica nada
reg.regularizar(b, servico, PASTAS, nome, "dependentes", "andre@ra", "conferido no portal.envio 472", True, b.evento)
est = servico.ler_estado(nome, PASTAS)
ok(len([e for e in est["envios"] if e["onda"] == "dependentes"]) == 1 and len(b.acomp) == 1 and len(b.notas) == 1,
   "C: rodar 2x não duplica")

# C2. acompanhamento da regularização que o vigia marcou FALHOU (1ª versão da ferramenta): conserta, sem criar outro
b.status = "FALHOU"
passos = reg.regularizar(b, servico, PASTAS, nome, "dependentes", "andre@ra", "conferido no portal.envio 472", True, b.evento)
ok(len(b.consertos) == 1 and b.consertos[0][:2] == ("1337619", QUANDO) and len(b.acomp) == 1,
   "C2: acompanhamento FALHOU da regularização volta a aguardar, com movimentação e horário")
ok(len(b.notas) == 2 and "Desconsiderar" in b.notas[1][3]["obs"], "C2: NOTA no chamado desmentindo o aviso falso")

# D. sem prova no portal: para sem mudar nada
nome = lote("D")
b = Banco(tem_prova=False)
try:
    reg.regularizar(b, servico, PASTAS, nome, "dependentes", "andre@ra", "conferido no portal.envio 472", True, b.evento)
    ok(False, "D: sem prova devia parar")
except SystemExit as e:
    ok("sem prova" in str(e) and len(servico.ler_estado(nome, PASTAS)["envios"]) == 1 and not b.acomp,
       "D: sem prova no portal.envio não regulariza")

# E. TXT trocado no disco: para
nome = lote("E")
open(os.path.join(TMP, nome, DEP["arquivo"]), "wb").write(b"outro conteudo\n")
try:
    reg.regularizar(Banco(), servico, PASTAS, nome, "dependentes", "andre@ra", "conferido no portal.envio 472", True)
    ok(False, "E: hash diferente devia parar")
except SystemExit as e:
    ok("hash diferente" in str(e), "E: TXT diferente do aprovado não regulariza")

print(json.dumps({"falhas": falhas}))
sys.exit(1 if falhas else 0)
