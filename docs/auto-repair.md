[[Voltar ao README]](../README.md) • [[Índice da documentação]](index.md)

# AUTO-REPAIR: Normalização, Enriquecimento e Telemetria

Este documento descreve as configurações e o comportamento do mecanismo de AUTO-REPAIR do projeto, incluindo normalizações, validações, quarentena e telemetria.

## Perfis de Reparação
- `AUTO_REPAIR_LEVEL` (`none`, `basic`, `aggressive`)
  - `none`: desativa normalizações no hook; somente validações posteriores (FK, constraints).
  - `basic`: normalizações essenciais (CNPJ partes com tamanho, CEP 8 dígitos, UF válida, CNAE principal 7 dígitos, emails básicos, arrays secundários em `{...}`); valores inválidos podem ser nulos.
  - `aggressive`: adiciona heurísticas (dedup/sort em CNAE secundário, email estrito, exemplos de E.164, enriquecimento por CEP e por nome do município) e telemetria detalhada.
    - Também passa a **validar comprimento**: `ddd_1`/`ddd_2`/`ddd_fax` só aceitam 2–3 dígitos e `telefone_1`/`telefone_2`/`fax` só 8–11; o que estiver fora vira `NULL`. É o filtro mais destrutivo da diferença entre `basic` e `aggressive`.

## Validações de Consistência
- CPF/CNPJ em `socios`: valida dígitos verificadores e inválidos viram `NULL`. **Não há telemetria para isso** — `invalid_ids`/`invalid_id_examples` só são emitidos para `estabelecimentos`.
- CNPJ em `estabelecimentos`: linhas com CNPJ completo inválido são sempre quarentenadas; elas só são **removidas da carga** quando `SKIP_INVALID_ESTABELECIMENTOS_CNPJ=true` (padrão: `false`).
- Duplicatas de chave primária: na carga, linhas cuja chave já apareceu são
  descartadas (mantém-se a primeira), com janela deslizante de 1.000.000 de chaves.
  O descarte é contado em `duplicate_key_rows_dropped`.

## Quarentena
- Saída: `logs/quarantine/<YYYYMMDD>/<config>.jsonl` (data em **UTC**; o nome do
  arquivo é o nome da config da tabela, não o nome da tabela)
- Razões:
  - `critical_fields_null`: identificadores críticos nulos por tabela
  - `fk_violation`: violações de domínio/FK (modo não estrito normaliza para `NULL`)
  - `invalid_cnpj`: CNPJ completo inválido em `estabelecimentos`
  - `unreadable_volume`: o arquivo não pôde ser lido (largura de coluna
    inconsistente, por exemplo). O volume é pulado e os demais continuam; o
    registro traz `volume` e `error`, porque um volume pulado sem rastro é
    indistinguível de um volume que nunca existiu
  - `quality_gate`: chunk descartado pelo gate — um registro agregado com
    `trigger_columns` e `rows`, **mais um registro por linha** com a linha completa,
    para permitir reprocessamento depois do saneamento
- Cada registro inclui linha completa, campos afetados, chunk e timestamp.

## Telemetria de Reparos
- Saída: `logs/telemetry/<YYYYMMDD>/<config>.jsonl` (data em **UTC**)
- > O diretório `logs/auto_repair/` **não existe mais**. A telemetria foi movida
  > para `logs/telemetry/` e a quarentena para `logs/quarantine/`; aquele diretório
  > era criado no boot e nunca recebia nada, então foi removido do código. Isso é
  > travado por teste em `tests/test_manifest_contract.py`.
- Conteúdo por chunk:
  - `level`: perfil ativo
  - `columns`: colunas do chunk
  - `after_nulls` e `null_deltas`: contagem de nulos por coluna e variação
  - `changed_counts` (só `aggressive`): valores alterados por coluna — é a entrada dos quality gates
  - `sample_diffs` (aggressive): exemplos `antes→depois` por coluna
  - `invalid_ids` e `invalid_id_examples` (só `estabelecimentos`): estatísticas de identificadores inválidos
  - `e164_examples` (aggressive): exemplos de canônicos `+55<DDD><Telefone>`
  - `cep_enrichment` (aggressive, e só quando houve correção): métricas e exemplos de inferência/correção

## Enriquecimento por CEP
- Flags:
  - `ENABLE_CEP_ENRICHMENT` (bool): ativa enriquecimento por dataset externo
  - `CEP_MAP_PATH`: CSV com mapeamentos (formatos abaixo). O mapa é lido uma vez e
    cacheado em memória — trocar a variável no mesmo processo não relê o CSV
  - `CEP_CORRECT_UF_ONLY_IF_NULL` (bool): se `true`, corrige `UF` apenas quando nula; se `false`, também corrige divergências
- Formatos aceitos:
  - Exato: colunas `cep`, `uf`, `municipio_codigo`
  - Prefixo: colunas `cep_prefix` (5 dígitos), `uf`, `municipio_codigo`
  - O mapa é lido como texto e a chave é normalizada dos dois lados, então um
    `cep` só com dígitos no CSV não quebra o merge. Use zero à esquerda de
    qualquer forma: `01234000` e `1234000` são CEPs diferentes para a Receita
- Uma linha só é alterada quando o mapa realmente casou com ela. CEP ausente
  no mapa deixa `uf`, `municipio_codigo` e osrespective `*_source` como estão
- Ações (`aggressive` **e** `ENABLE_CEP_ENRICHMENT`):
  - Preenche `municipio_codigo` ausente; define `municipio_source` como `cep_map_exact` ou `cep_map_prefix`
  - Preenche/ajusta `uf` conforme `CEP_CORRECT_UF_ONLY_IF_NULL`; define `uf_source`

## Mapeamento por Nome do Município
- Flag: `MUNICIPIO_NAME_MAP_PATH`: CSV com `municipio_nome`, `municipio_codigo` e opcional `uf`
- Ação (**não é exclusiva do `aggressive`** — roda em `basic` também):
  - Infere `municipio_codigo` ausente por match de `municipio_nome` (e `uf` quando presente); define `municipio_source = mun_name_map`

## Backfill de Domínios
- `ENABLE_CONSTRAINTS_BACKFILL` (bool, padrão `true`): faz o `src/backfill.py` criar os
  registros pai ausentes entre as fases da carga. Os literais gravados são
  `NAO CONSTA NA ORIGEM` e, para `empresas`, `EMPRESA INEXISTENTE NA ORIGEM - GERADA AUTOMATICAMENTE`.
  - `true`: habilita o backfill pré-carga dos filhos
  - `false`: desabilita; use apenas quando os domínios oficiais já estiverem completos
    e carregados, senão a carga falha na primeira referência órfã
- > **Por que o backfill roda antes dos filhos.** O DuckDB não faz
  > `ALTER TABLE ADD FOREIGN KEY`: as FKs são declaradas dentro do `CREATE TABLE`
  > (`src/schema.sql`) e são impostas já no `INSERT`. Não existe carregar tudo e
  > restringir depois, então o backfill precisa rodar **antes** das tabelas
  > filhas — se não, a carga aborta na primeira referência órfã.

## Tipos e Esquema
- `estabelecimentos.cnae_fiscal_secundaria`: `VARCHAR` guardando o literal
  `"{1234567,9876543}"` — **não** é `INT[]`. O DuckDB não converte o literal de brace
  em `LIST`, e manter a coluna como texto deixa os cleaners de `validation.py` intactos
- `estabelecimentos.uf_source` e `municipio_source`: rastreiam origem de correções
- `estabelecimentos.municipio_nome`: armazenado para análises e enriquecimentos

## Rotação e Cotas
- `TELEMETRY_MAX_BYTES`, `QUARANTINE_MAX_BYTES`: tamanho máximo por arquivo `.jsonl`.
  Ao estourar, o próximo arquivo ganha sufixo `_1`, `_2`, … em vez de truncar
- `TELEMETRY_ROTATE_DAILY`, `QUARANTINE_ROTATE_DAILY`: rotação diária de diretórios,
  com a data em UTC (`YYYYMMDD`)

## Portas de Qualidade (Quality Gates)
- `ENABLE_QUALITY_GATES`: ativa verificação por chunk
- `GATE_MAX_CHANGED_RATIO`: razão máxima de valores alterados por coluna
- `GATE_MAX_NULL_DELTA_RATIO`: razão máxima de variação de nulos por coluna
- `GATE_LOG_LEVEL`: nível de log (`INFO` ou `WARNING`) quando o gate dispara; padrão `WARNING`
- `GATE_MIN_ROWS`: mínimo de linhas no chunk para aplicar gates
- Ao disparar, o chunk é pulado; um registro `quality_gate` é escrito em telemetria e quarentena.

## Sumário e Métricas
- Sumário por tabela: `logs/telemetry/<YYYYMMDD>/<config>_summary.json` com:
  - `rows_read` (linhas lidas do CSV), `volumes_read` (volumes abertos) e
    `rows_total` (linhas que entraram na tabela) — é a reconciliação com a origem
  - `rows_discarded` (`rows_read - rows_total`)
  - `chunks_processed`, `quality_gate_chunks`
  - `quality_gate_rows_discarded`, `invalid_cnpj_rows_skipped`,
    `critical_key_null_rows_skipped`, `duplicate_key_rows_dropped`
  - `changed_totals`, `null_delta_totals`
  - `column_stats` por coluna: `min`, `max`, `nulls` pós-reparo
  - `load_strategy`: `duckdb_native` ou `pandas_chunked` — qual caminho carregou a tabela
  - `volumes_failed` / `failed_volumes`: volumes que não puderam ser lidos. Um
    valor diferente de zero significa que a carga está **incompleta**: confira o
    registro `unreadable_volume` na quarentena antes de confiar na tabela
- Métricas Prometheus (opcional):
  - `ENABLE_METRICS_PROMETHEUS` e `PROMETHEUS_METRICS_PATH` (arquivo de saída `.prom`)
  - Expostos como contadores simples:
    - `cnpj_auto_repair_rows_total{table="..."}`
    - `cnpj_auto_repair_quality_gate_chunks_total{table="..."}`
    - `cnpj_auto_repair_changed_total{table="...",column="..."}`
    - `cnpj_auto_repair_null_delta_total{table="...",column="..."}`
  - Push para Pushgateway:
    - `ENABLE_PROMETHEUS_PUSH`, `PROMETHEUS_PUSH_URL`, `PROMETHEUS_JOB`, `PROMETHEUS_INSTANCE`
  - OpenTelemetry (opcional):
    - `ENABLE_OTLP_PUSH`, `OTLP_ENDPOINT` (POST JSON de sumário)

> Todas essas variáveis existem em `Settings` e aparecem no `.env.example` —
> as opcionais estão comentadas lá, com o valor padrão anotado. A lista canônica
> é `src/settings.py`; se as duas divergirem, o `.env.example` está errado.

## Exemplos de Configuração (.env)
```
AUTO_REPAIR_LEVEL=aggressive
STRICT_FK_VALIDATION=false
ENABLE_CEP_ENRICHMENT=true
CEP_MAP_PATH=./data/cep_map.csv
CEP_CORRECT_UF_ONLY_IF_NULL=true
MUNICIPIO_NAME_MAP_PATH=./data/municipios_por_nome.csv
ENABLE_CONSTRAINTS_BACKFILL=false
TELEMETRY_MAX_BYTES=10000000
QUARANTINE_MAX_BYTES=10000000
TELEMETRY_ROTATE_DAILY=true
QUARANTINE_ROTATE_DAILY=true
```

## Boas Práticas
- Prefira domínios oficiais completos e desative backfill quando possível.
- Use `aggressive` apenas quando desejar correções heurísticas e telemetria detalhada.
- Audite a quarentena regularmente e, se necessário, reprocessar linhas após saneamento.
