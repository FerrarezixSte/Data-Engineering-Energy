-- Silver: geração horária válida, sem duplicatas por (usina, instante).
select
    instante,
    year(instante)  as ano,
    month(instante) as mes,
    chave_usina, usina, tipo_fonte, estado, subsistema,
    geracao_mw,
    source_file, ingested_at
from {{ ref('int_geracao_avaliada') }}
where motivo_rejeicao is null and ordem_duplicata = 1
