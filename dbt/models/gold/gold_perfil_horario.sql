-- Curva típica do dia: MW médio por hora, por fonte e estado, e o total Brasil.
with por_dia as (
    select tipo_fonte, coalesce(estado, 'N/D') as estado, ano, mes, cast(instante as date) as data,
           hour(instante) as hora, sum(geracao_mw) as mw
    from {{ ref('silver_geracao_usina') }}
    where tipo_fonte in ('SOLAR', 'EOLICA')
    group by all
),
brasil as (
    select tipo_fonte, 'BRASIL' as estado, ano, mes, data, hora, sum(mw) as mw
    from por_dia group by all
)
select tipo_fonte, estado, ano, mes, hora,
       round(avg(mw), 2) as mw_medio,
       round(max(mw), 2) as mw_maximo
from (select * from por_dia union all select * from brasil)
group by all
