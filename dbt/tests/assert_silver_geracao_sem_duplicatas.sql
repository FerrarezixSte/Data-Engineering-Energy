-- Falha se existir mais de uma linha por (usina, instante) na Silver.
select chave_usina, usina, instante, count(*) as n
from {{ ref('silver_geracao_usina') }}
group by all
having count(*) > 1
