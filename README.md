# hapvida-check

Automação diária de checagem de comissões Hapvida em aberto para emissão de
NF, por código de concessionária. Roda como um container HTTP próprio,
disparado pelo n8n (`Schedule Trigger` + `HTTP Request`), grava histórico em
Postgres e dispara um e-mail consolidado para financeiro@ra-beneficios.com.br
apenas quando há algo novo ou atrasado.

## Como adicionar uma nova concessionária

Só criar um secret no Secret Manager com o prefixo `hapvida-` — nada no
código muda, o script descobre os códigos automaticamente.

```bash
echo -n '{"usuario":"128A","senha":"SUA_SENHA_AQUI"}' \
  | gcloud secrets create hapvida-128a --data-file=- --project=lifetools-ra
```

Para atualizar a senha de um código existente (nova versão do secret):

```bash
echo -n '{"usuario":"128A","senha":"NOVA_SENHA"}' \
  | gcloud secrets versions add hapvida-128a --data-file=- --project=lifetools-ra
```

## Rodando localmente / rebuild

```bash
cd /opt/hapvida-check
cp .env.example .env   # preencher PGPASSWORD real
docker compose build
docker compose up -d
docker compose logs -f
```

O container se conecta a duas redes Docker externas:
- `n8n_default` — para o n8n chamar `http://hapvida-check:8000/run` via DNS interno.
- `lifetools_default` — para alcançar o Postgres (`lifetools-postgres-1`, host `postgres`).

Teste manual do endpoint:

```bash
docker exec -it n8n wget -qO- --post-data='' http://hapvida-check:8000/run
```

## Workflow n8n

Importar `n8n/hapvida-check.json` no n8n (Workflows > Import from File). O
workflow: Schedule Trigger diário (7h) → HTTP Request `POST` para
`http://hapvida-check:8000/run` → IF (`novos_count > 0 OR atrasados_count > 0`)
→ Code (monta o HTML do e-mail) → Outlook (envia para
financeiro@ra-beneficios.com.br usando a credencial já existente
`Microsoft Outlook account`). Se não houver nada novo/atrasado, o IF barra o
fluxo e nenhum e-mail é enviado. Depois de importar, ativar o workflow.

## Banco de dados

Tabela `hapvida_comissoes` no banco `hapvida` dentro do container
`lifetools-postgres-1` (usuário dedicado `hapvida_check`, isolado do `radb`
usado pelo projeto lifetools). Ver `db/schema.sql`.

## Onde entraria uma nova operadora

Hoje só existe `HapvidaAdapter` em `scraper.py`, usando o prefixo de secret
`hapvida-`. Uma operadora nova teria seu próprio prefixo (ex: `operadorax-`)
e sua própria classe com `login()` / `fetch_open_invoices()`, reaproveitando
`upsert_pedidos` e `build_summary`. Não implementado agora — fora de escopo.

## Troubleshooting

- **"SESSÃO EXPIROU" recorrente**: o adapter já refaz login automaticamente
  (até 2 tentativas por ano consultado). Se persistir, o erro aparece na
  lista `erros` do JSON de resposta do `/run`, sem derrubar as outras
  concessionárias.
- **Falha ao acessar o Secret Manager**: confirme que a API está habilitada
  (`gcloud services enable secretmanager.googleapis.com`) e que a instância
  GCE tem o escopo `cloud-platform` (não só os escopos default). A
  autenticação usa Application Default Credentials via metadata server —
  não há chave JSON para gerenciar.
- **Secret mal formatado**: o erro aparece por código na lista `erros`, sem
  interromper os demais.
- **Nunca chega e-mail**: por design, só é enviado quando há pedidos "novos
  hoje" ou "atrasados" — ausência de e-mail é o comportamento esperado
  quando não há nada a reportar.
