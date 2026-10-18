-- Energia diária de cada usina solar e eólica.
select
    chave_usina,
    cast(instante as date)                    as data,
    any_value(usina)                          as usina,
    any_value(tipo_fonte)                     as tipo_fonte,
    any_value(estado)                         as estado,
    any_value(subsistema)                     as subsistema,
    round(sum(geracao_mw), 2)                 as energia_mwh,
    round(max(geracao_mw), 2)                 as pico_mw,
    count(*) filter (where geracao_mw > 0)    as horas_gerando
from {{ ref('silver_geracao_usina') }}
where tipo_fonte in ('SOLAR', 'EOLICA')
group by chave_usina, cast(instante as date)
