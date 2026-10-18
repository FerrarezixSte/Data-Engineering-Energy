-- Silver: fator de capacidade de usinas/conjuntos eólicos e solares, validado e sem duplicatas.
select
    instante, year(instante) as ano, month(instante) as mes,
    chave_conjunto, conjunto, tipo_fonte, estado,
    capacidade_mw, geracao_verificada_mw, geracao_programada_mw, fator_capacidade,
    latitude, longitude,
    source_file, ingested_at
from {{ ref('int_fator_avaliada') }}
where motivo_rejeicao is null and ordem_duplicata = 1
