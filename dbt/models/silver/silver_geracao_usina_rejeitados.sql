-- Quarentena: linhas que violaram regra ou cópias duplicadas, com o motivo.
select
    * exclude (ordem_duplicata, motivo_rejeicao),
    coalesce(motivo_rejeicao, 'duplicado (usina, instante): cópia mais antiga') as motivo_rejeicao
from {{ ref('int_geracao_avaliada') }}
where motivo_rejeicao is not null or ordem_duplicata > 1
