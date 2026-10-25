-- Regra física: usina solar não gera à noite.
-- Aponta usinas solares (fora os agregados de MMGD, que são estimativas por estado) com geração
-- relevante entre 0h e 4h. É aviso, não erro: o ONS às vezes publica ruído de poucos MW.
{{ config(severity='warn') }}
select chave_usina, any_value(usina) as usina,
       round(avg(geracao_mw) filter (where hour(instante) between 0 and 4), 2) as mw_medio_madrugada,
       round(max(geracao_mw), 2) as pico_mw
from {{ ref('silver_geracao_usina') }}
where tipo_fonte = 'SOLAR' and upper(coalesce(usina, '')) not like '%MMGD%'
group by chave_usina
having avg(geracao_mw) filter (where hour(instante) between 0 and 4) > 0.02 * max(geracao_mw)
