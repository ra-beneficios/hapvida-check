-- Obra 2 (17/10/2026, docs/processos/OBRAS_ESCALA_100K.md no repo lifetools): todo upload ao portal Hapvida fica
-- registrado ANTES de subir. Reiniciar no meio não perde nem duplica: o reconciliador confere pelo nome no portal.
-- Só acréscimo (código antigo segue funcionando sem a tabela).
CREATE SCHEMA IF NOT EXISTS portal;
CREATE TABLE IF NOT EXISTS portal.envio (
  id            bigserial PRIMARY KEY,
  tipo          text NOT NULL,            -- INCLUSAO | REENVIO | EXCLUSAO
  contrato      text NOT NULL,
  nome_portal   text NOT NULL,            -- nome do arquivo que sobe (vira o uploadName no portal)
  sha256        char(64) NOT NULL,
  linhas        int NOT NULL,
  estado        text NOT NULL,            -- ENVIANDO | ENTREGUE | NO_PORTAL | INCERTO | FALHOU_UPLOAD | FALHOU | DUPLICADO_BLOQUEADO
  protocolo     text,                     -- controlCode da movimentação no portal (conferido na lista)
  status_portal text,                     -- status da movimentação quando conferida (ex.: UnableToStart)
  confirmacao   text,                     -- o que o upload devolveu (body.content)
  http_status   int,
  erro          text,
  verificacoes  int NOT NULL DEFAULT 0,
  criado_em     timestamptz NOT NULL DEFAULT now(),
  atualizado_em timestamptz NOT NULL DEFAULT now(),
  conferido_em  timestamptz
);
CREATE INDEX IF NOT EXISTS ix_envio_estado ON portal.envio (estado, criado_em);
CREATE INDEX IF NOT EXISTS ix_envio_dedup ON portal.envio (contrato, sha256, criado_em);
