-- Falha se a participação das fontes num mês não somar ~100%.
select ano, mes, sum(participacao_pct) as total
from {{ ref('gold_matriz_mensal') }}
group by all
having abs(sum(participacao_pct) - 100) > 0.5
