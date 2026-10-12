[[Voltar ao README]](../README.md) • [[Índice da documentação]](index.md)

# O caminho de carga

A carga tem dois caminhos, escolhidos por tabela. A escolha é **derivada**, não
marcada: `src/database_loader.py:_native_eligible` decide a partir do que a
tabela declara, de modo que uma tabela que ganhe um schema depois deixa de se
qualificar sozinha, em vez de pular a validação silenciosamente porque alguém
esqueceu de desmarcar.

| Caminho | Quando | O que faz |
|---|---|---|
| `duckdb_native` | sem schema em `validation.SCHEMA_MAP`, sem `custom_clean_func`, sem `dtype_map` | DuckDB lê, faz o cast, deduplica e grava |
| `pandas_chunked` | as quatro tabelas com limpeza/validação por linha | CSV → pandas → validação → DuckDB |

São `pandas_chunked` hoje: `empresas`, `estabelecimentos`, `socios` e
`simples`. Os cleaners e os quality gates operam em DataFrame, então o
DataFrame é realmente necessário ali. As tabelas de domínio (`paises`,
`municipios`, `qualificacoes`, `naturezas`, `cnaes`) não têm limpeza nem
validação alguma, e vão pelo caminho nativo.

O sumário de cada tabela registra qual dos dois a produziu, em `load_strategy`.

## Por que o SELECT é montado como texto

`db.csv_source_sql` devolve o `SELECT` como **string** mais os parâmetros, e não
uma relação DuckDB. Isso não é estilo: `rel.select(...)` e `rel.sql_query()`
sobre uma relação de `read_csv` **materializam o arquivo**, e o `sql_query`
serializa as linhas literalmente dentro da string. Com as ~100 GB do material da
Receita, seria exatamente o custo que o caminho nativo existe para remover — e
com o arquivo inteiro passando pelo cliente, em vez do motor.

O caminho parametrizado tem uma consequência prática: o volume só é lido quando
algo executa a query, e `CREATE OR REPLACE TEMP TABLE` lê uma vez só. A contagem,
os `column_stats` e o `INSERT` saem todos do stage.

## Três armadilhas do leitor

Todas foram descobertas executando o leitor, não lendo a documentação, e todas
falham em silêncio. Cada uma tem teste em `tests/test_native_load.py`.

**O BOM UTF-8 lido como latin-1 não é `U+FEFF`.** Cada um dos três bytes vira um
caractere latin-1 — `U+00EF U+00BB U+00BF`. Um `ltrim(col, '\ufeff')` não casa
com nada e falha em silêncio: o primeiro valor de cada volume fica poluído com o
BOM colado, e o primeiro código primário vira lixo. `db.BOM_LATIN1` traz a
leitura correta, e um teste compara as duas para a constante não ser "corrigida"
de novo. É a mesma razão pela qual `_open_volume` remove os bytes crus antes de
decodificar no caminho pandas.

**`con.read_csv()` só aceita UTF-8.** O método Python desce por `COPY`, que não
tem `ENCODING`. A função SQL `read_csv()` é a que aceita, e é a que o projeto
usa.

**`INSERT OR IGNORE` cobre conflito de chave, não violação de `NOT NULL`.** É
exatamente o que o `_drop_duplicate_keys` fazia em Python — mantém a primeira
ocorrência, descarta as repetidas — mas uma linha com chave nula ainda aborta a
carga, como antes. O `count_source` antes e depois dá o número de linhas que
aterrissaram sem trazer o resultado de volta para o cliente.

## Volume ilegível

Um volume que o leitor não consegue fazer sentido derrubaria a carga inteira, e
a quarentena — feita para exatamente esse caso — nunca veria a chance de rodar.
O volume é pulado, registrado em `logs/quarantine/` como `unreadable_volume` e
contabilizado em `volumes_failed` / `failed_volumes` no sumário.

A seletividade é o ponto. Um gerador que emite o chunk **dentro** do `try`
captura também o que o consumidor levanta, e a carga passaria por cima de uma
violação de constraint que precisa parar o run. Por isso o `yield` fica fora de
qualquer `try` e só a leitura vira `_VolumeReadError`. O comportamento é
testado dos dois lados em `tests/test_engine_edges.py`: volume malformado não
custa os volumes bons, e um erro depois da leitura continua abortando.

Um `volumes_failed` diferente de zero significa carga **incompleta** — Confira o
registro em quarentena antes de confiar na tabela.

## O resto da carga

Os passos, a ordem e as flags estão em [auto-repair.md](auto-repair.md); a
escolha de índices está em [boas-praticas-indices.md](boas-praticas-indices.md).