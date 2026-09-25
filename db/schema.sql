CREATE TABLE IF NOT EXISTS hapvida_comissoes (
    id SERIAL PRIMARY KEY,
    codigo_concessionaria VARCHAR(20) NOT NULL,
    ano_ref INT NOT NULL,
    numero_pedido VARCHAR(50) NOT NULL,
    lote VARCHAR(50),
    data_integracao DATE NOT NULL,
    valor NUMERIC(14,2) NOT NULL,
    data_primeira_deteccao DATE NOT NULL,
    data_ultima_verificacao DATE NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'aberto', -- 'aberto' ou 'pago'
    data_baixa DATE,
    UNIQUE(codigo_concessionaria, numero_pedido, lote, ano_ref)
);

CREATE INDEX IF NOT EXISTS idx_hapvida_status
    ON hapvida_comissoes (codigo_concessionaria, status, ano_ref);
