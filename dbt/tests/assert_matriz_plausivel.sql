-- Aviso de plausibilidade da matriz, comparando com a ordem de grandeza do SIN:
-- um mês completo gera bem mais de 40 TWh e a eólica passa de 3% da energia.
-- Se disparar, parte das usinas está se perdendo na ingestão ou na deduplicação.
{{ config(severity='warn') }}
with meses as (
    select ano, mes, sum(energia_gwh) as total_gwh,
           sum(energia_gwh) filter (where tipo_fonte = 'EOLICA') as eolica_gwh
    from {{ ref('gold_matriz_mensal') }}
    group by ano, mes
),
completos as (  -- ignora o mês corrente, que ainda está pela metade
    select * from meses
    where make_date(ano, mes, 1) < (select date_trunc('month', max(instante)) from {{ ref('silver_geracao_usina') }})
)
select ano, mes, round(total_gwh, 0) as total_gwh, round(100 * eolica_gwh / total_gwh, 2) as pct_eolica
from completos
where total_gwh < 40000 or coalesce(eolica_gwh, 0) < 0.03 * total_gwh
