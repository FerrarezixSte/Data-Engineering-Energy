-- Energia eólica que deixou de ser gerada por restrição de operação do ONS, por usina e mês.
with m as (
    select chave_usina, ano, mes,
           any_value(usina)  as usina,
           any_value(estado) as estado,
           sum(geracao_mw * horas_intervalo)                              as energia_gerada_mwh,
           sum(restringido_mw * horas_intervalo)                          as energia_restringida_mwh,
           sum(case when com_restricao then horas_intervalo else 0 end)   as horas_com_restricao
    from {{ ref('silver_restricao_eolica') }}
    group by all
)
select chave_usina, ano, mes, usina, estado,
       round(energia_gerada_mwh, 1)      as energia_gerada_mwh,
       round(energia_restringida_mwh, 1) as energia_restringida_mwh,
       horas_com_restricao,
       round(100 * energia_restringida_mwh / nullif(energia_gerada_mwh + energia_restringida_mwh, 0), 2) as restricao_pct
from m
