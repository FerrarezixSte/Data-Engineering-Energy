-- Aviso: uma mesma chave de usina com muitos nomes diferentes indica identificador genérico no ONS
-- (CEG "-" ou vazio, por exemplo) juntando usinas que não têm relação entre si.
{{ config(severity='warn') }}
select chave_usina, count(distinct usina) as nomes, min(usina) as exemplo_1, max(usina) as exemplo_2
from {{ ref('silver_geracao_usina') }}
group by chave_usina
having count(distinct usina) > 3
