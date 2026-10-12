-- ============================================================================
-- SCHEMA RFB — DuckDB
-- ============================================================================
-- Decisões do schema, e o que as impõe:
--
--   * Sem CREATE SCHEMA. O DuckDB não tem schemas: as tabelas vivem em `main`.
--   * Sem tabelas não duráveis. O DuckDB é sempre WAL-backed, então uma queda
--     de processo não deixa tabela truncada.
--   * As chaves estrangeiras moram aqui, dentro do CREATE TABLE. O DuckDB não
--     implementa ALTER TABLE ADD FOREIGN KEY, então não existe "carrega tudo e
--     restringe depois": a ordem de carga tem que ser pai antes de filho, que é
--     o que run_loader já garante.
--   * As tabelas de domínio carregam PRIMARY KEY porque o DuckDB só aceita uma
--     chave primária ou uma coluna única como alvo de chave estrangeira.
--   * cnae_fiscal_secundaria fica em VARCHAR guardando o literal de chaves
--     ("{1234567,9876543}") que vem no CSV da Receita. O DuckDB não converte
--     esse formato em LIST, e manter a coluna como texto deixa os cleaners de
--     validation.py intactos.
--
-- O DROP é decidido em Python (settings.allow_drop), que decide se este arquivo
-- chega a rodar.
-- ============================================================================

-- ============================================================================
-- 1. TABELAS DE DOMINIO
-- ============================================================================
-- Declared first: everything else references them.

CREATE TABLE IF NOT EXISTS paises (
    codigo SMALLINT PRIMARY KEY,
    nome VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS municipios (
    codigo INTEGER PRIMARY KEY,  -- codigo SIAFI, 7 digitos (ex.: 3550308)
    nome VARCHAR(150)
);

CREATE TABLE IF NOT EXISTS qualificacoes_socios (
    codigo SMALLINT PRIMARY KEY,
    nome VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS naturezas_juridicas (
    codigo SMALLINT PRIMARY KEY,
    nome VARCHAR(150)
);

CREATE TABLE IF NOT EXISTS cnaes (
    codigo INTEGER PRIMARY KEY, -- 7 digitos (ex: 4711302)
    nome VARCHAR(500)
);

-- ============================================================================
-- 2. TABELAS PRINCIPAIS
-- ============================================================================

CREATE TABLE IF NOT EXISTS empresas (
    cnpj_basico VARCHAR(8) PRIMARY KEY,
    razao_social VARCHAR(255),
    natureza_juridica_codigo SMALLINT REFERENCES naturezas_juridicas (codigo),
    qualificacao_responsavel SMALLINT REFERENCES qualificacoes_socios (codigo),
    capital_social DECIMAL(18, 2),
    porte_empresa SMALLINT,
    ente_federativo_responsavel VARCHAR(100)
);

CREATE TABLE IF NOT EXISTS estabelecimentos (
    cnpj_basico VARCHAR(8) NOT NULL,
    cnpj_ordem VARCHAR(4) NOT NULL,
    cnpj_dv VARCHAR(2) NOT NULL,
    identificador_matriz_filial SMALLINT,
    nome_fantasia VARCHAR(255),
    situacao_cadastral SMALLINT,
    data_situacao_cadastral DATE,
    motivo_situacao_cadastral SMALLINT,
    nome_cidade_exterior VARCHAR(100),
    pais_codigo SMALLINT REFERENCES paises (codigo),
    data_inicio_atividade DATE,
    cnae_fiscal_principal_codigo INTEGER REFERENCES cnaes (codigo),
    cnae_fiscal_secundaria VARCHAR,   -- literal "{1234567,9876543}"
    tipo_logradouro VARCHAR(50),
    logradouro VARCHAR(255),
    numero VARCHAR(20),
    complemento VARCHAR(255),
    bairro VARCHAR(100),
    cep VARCHAR(8),
    uf VARCHAR(2),
    municipio_codigo INTEGER REFERENCES municipios (codigo),
    ddd_1 VARCHAR(4),
    telefone_1 VARCHAR(9),
    ddd_2 VARCHAR(4),
    telefone_2 VARCHAR(9),
    ddd_fax VARCHAR(4),
    fax VARCHAR(9),
    correio_eletronico VARCHAR(255),
    situacao_especial VARCHAR(100),
    data_situacao_especial DATE,
    municipio_nome VARCHAR(150),
    uf_source VARCHAR(20),
    municipio_source VARCHAR(20),
    PRIMARY KEY (cnpj_basico, cnpj_ordem, cnpj_dv),
    FOREIGN KEY (cnpj_basico) REFERENCES empresas (cnpj_basico)
);

CREATE TABLE IF NOT EXISTS simples (
    cnpj_basico VARCHAR(8) PRIMARY KEY REFERENCES empresas (cnpj_basico),
    opcao_pelo_simples CHAR(1),
    data_opcao_pelo_simples DATE,
    data_exclusao_do_simples DATE,
    opcao_pelo_mei CHAR(1),
    data_opcao_pelo_mei DATE,
    data_exclusao_do_mei DATE
);

CREATE TABLE IF NOT EXISTS socios (
    cnpj_basico VARCHAR(8) NOT NULL REFERENCES empresas (cnpj_basico),
    identificador_socio SMALLINT, -- 1, 2 ou 3
    nome_socio_ou_razao_social VARCHAR(255),
    cnpj_cpf_socio VARCHAR(14),   -- CPF (11) ou CNPJ (14)
    qualificacao_socio_codigo SMALLINT REFERENCES qualificacoes_socios (codigo),
    data_entrada_sociedade DATE,
    pais_codigo SMALLINT REFERENCES paises (codigo),
    representante_legal_cpf VARCHAR(11),
    nome_representante_legal VARCHAR(255),
    qualificacao_representante_legal_codigo SMALLINT REFERENCES qualificacoes_socios (codigo),
    faixa_etaria SMALLINT         -- 0 a 9
);

-- ============================================================================
-- 3. INDICES
-- ============================================================================
-- Só índices secundários. O DuckDB não tem operador de substring indexado, e
-- não precisa: o DuckDB é columnar, então a busca por substring em razao_social
-- é uma varredura de coluna medida em cerca de dois segundos em 60M linhas.

CREATE INDEX IF NOT EXISTS idx_empresas_razao_social ON empresas (razao_social);
CREATE INDEX IF NOT EXISTS idx_empresas_natureza ON empresas (natureza_juridica_codigo);
CREATE INDEX IF NOT EXISTS idx_empresas_qualificacao ON empresas (qualificacao_responsavel);

CREATE INDEX IF NOT EXISTS idx_estabelecimentos_cnae_main ON estabelecimentos (cnae_fiscal_principal_codigo);
CREATE INDEX IF NOT EXISTS idx_estabelecimentos_municipio ON estabelecimentos (municipio_codigo);
CREATE INDEX IF NOT EXISTS idx_estabelecimentos_uf ON estabelecimentos (uf);
CREATE INDEX IF NOT EXISTS idx_estabelecimentos_nome_fantasia ON estabelecimentos (nome_fantasia);

CREATE INDEX IF NOT EXISTS idx_socios_cnpj_basico ON socios (cnpj_basico);
CREATE INDEX IF NOT EXISTS idx_socios_cpf_cnpj ON socios (cnpj_cpf_socio);
CREATE INDEX IF NOT EXISTS idx_socios_nome ON socios (nome_socio_ou_razao_social);