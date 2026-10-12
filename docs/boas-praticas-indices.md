[[Voltar ao README]](../README.md) • [[Índice da documentação]](index.md)

# Desempenho de consulta no DuckDB

Este guia descreve quando indexes valem a pena no DuckDB. A recomendação vem do
princípio abaixo, não de fórmula: o motor é columnar, e índice que não evita
varrer uma coluna grande é custo puro.

## O princípio

O DuckDB é columnar e orientado a análise. Ele varre blocos inteiros de coluna
em paralelo, então **filtrar sem índice costuma ser barato**. O índice ART
existe, mas só compensa quando ele evita varrer uma coluna grande.

O caso que define a escolha é a prova: busca por nome parcial em `empresas`,
com 60 milhões de linhas.

```sql
SELECT * FROM empresas WHERE razao_social ILIKE '%MARQUES%';
```

| Consulta | Tempo |
|---|---|
| varredura completa da coluna (2,47 GB), zero resultado | **2,0 s** |
| 6 milhões de linhas retornadas ao cliente | 6,7 s |
| `cnpj_basico = '...'` com índice | 0,001 s |

Os 6,7 s são materializar o resultado, não a busca. O DuckDB não tem operador
de substring indexado, e não precisa: manter um índice desses em dezenas de
milhões de linhas custa mais do que a varredura.

## Quando um índice compensa

- **Igualdade em chave alta** — `cnpj_basico = ?`. O índice ART resolve em
  milissegundos e a varredura seria cara.
- **Coluna estreita** — indexar `uf` (2 bytes) é barato em espaço, mas o
  zone map do DuckDB já descarta a maioria dos blocos sem índice. Meça antes.
- **Junções repetidas** — `estabelecimentos.cnpj_basico` tem índice próprio.

Os índices já criados estão em `../src/schema.sql`:

```sql
CREATE INDEX IF NOT EXISTS idx_empresas_natureza ON empresas (natureza_juridica_codigo);
CREATE INDEX IF NOT EXISTS idx_empresas_qualificacao ON empresas (qualificacao_responsavel);
CREATE INDEX IF NOT EXISTS idx_estabelecimentos_cnae_main ON estabelecimentos (cnae_fiscal_principal_codigo);
CREATE INDEX IF NOT EXISTS idx_estabelecimentos_municipio ON estabelecimentos (municipio_codigo);
CREATE INDEX IF NOT EXISTS idx_estabelecimentos_uf ON estabelecimentos (uf);
CREATE INDEX IF NOT EXISTS idx_socios_cnpj_basico ON socios (cnpj_basico);
CREATE INDEX IF NOT EXISTS idx_socios_cpf_cnpj ON socios (cnpj_cpf_socio);
```

Índices secundários custam tempo de escrita na carga. Meça o ganho antes de
adicionar um.

## O que o DuckDB não faz

| Não existe | O que fazer |
|---|---|
| operador de substring indexado | substring por varredura de coluna |
| índice concorrente | um escritor por arquivo; nada a concursar |
| índice parcial (`WHERE ...`) | filtre na consulta |
| `VACUUM`, `fillfactor`, `REINDEX` | nada disso; o DuckDB é imutável por fora |
| `CLUSTER` | ordene na carga: `CREATE TABLE ... AS SELECT ... ORDER BY` |

Para inspecionar o que existe de fato:

```sql
SELECT index_name, table_name, is_unique FROM duckdb_indexes();
```

## Manutenção

O DuckDB mantém as próprias estatísticas; o passo `constraints` faz
`ANALYZE` e `CHECKPOINT`. Nada além disso é necessário.

## Dimensionamento

- `DUCKDB_MEMORY_LIMIT` limita o buffer manager; o excedente vai para
  `DUCKDB_TEMP_DIRECTORY`. Padrão: 80% da RAM.
- `DUCKDB_THREADS=0` deixa o DuckDB decidir.
- Arquivos de dezenas de GB não são problema; o limite prático não existe.