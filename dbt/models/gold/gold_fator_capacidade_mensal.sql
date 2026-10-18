-- Fator de capacidade médio por usina/conjunto e mês (eólica e solar).
select
    chave_conjunto, ano, mes,
    any_value(conjunto)                          as conjunto,
    any_value(tipo_fonte)                        as tipo_fonte,
    any_value(estado)                            as estado,
    round(max(capacidade_mw), 2)                 as capacidade_mw,
    round(100 * avg(fator_capacidade), 2)        as fator_capacidade_pct,
    round(sum(geracao_verificada_mw), 1)         as energia_mwh,
    any_value(latitude)                          as latitude,
    any_value(longitude)                         as longitude
from {{ ref('silver_fator_capacidade') }}
group by chave_conjunto, ano, mes
