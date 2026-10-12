# Consultas SQL executadas ao final do pipeline por `--run-queries`.
#
# Coloque aqui arquivos `.sql`; eles rodam em ordem alfabético, contra o mesmo
# DuckDB do pipeline, depois da etapa `load`. Sem nenhum arquivo, a flag não faz
# nada (`run_queries_in_dir` apenas avisa).
#
# Exemplos de uso:
#   ranking de empresas por porte
#     SELECT porte_empresa, COUNT(*) FROM empresas GROUP BY 1 ORDER BY 2 DESC;
#
#   QA de faixa de UF
#     SELECT uf, COUNT(*) FROM estabelecimentos GROUP BY 1 ORDER BY 2 DESC;