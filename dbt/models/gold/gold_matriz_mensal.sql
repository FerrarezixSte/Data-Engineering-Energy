-- Energia por fonte no mês e participação na matriz. Geração horária em MWmed = MWh daquela hora.
with e as (
    select ano, mes, tipo_fonte, sum(geracao_mw) as energia_mwh
    from {{ ref('silver_geracao_usina') }}
    group by all
)
select
    ano, mes, tipo_fonte,
    round(energia_mwh, 1)                                                       as energia_mwh,
    round(energia_mwh / 1000, 3)                                                as energia_gwh,
    round(100 * energia_mwh / sum(energia_mwh) over (partition by ano, mes), 2) as participacao_pct
from e
