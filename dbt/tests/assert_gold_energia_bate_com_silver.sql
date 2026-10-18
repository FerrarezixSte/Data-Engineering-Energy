-- Falha se a energia solar+eólica da Gold diária divergir da Silver em mais de 0,1%.
with s as (select sum(geracao_mw) e from {{ ref('silver_geracao_usina') }} where tipo_fonte in ('SOLAR','EOLICA')),
     g as (select sum(energia_mwh) e from {{ ref('gold_geracao_diaria_usina') }})
select s.e as silver, g.e as gold
from s, g
where abs(coalesce(s.e, 0) - coalesce(g.e, 0)) > 0.001 * greatest(coalesce(s.e, 0), 1)
