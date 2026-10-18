select
    * exclude (ordem_duplicata, motivo_rejeicao, fator_bruto),
    coalesce(motivo_rejeicao, 'duplicado (conjunto, instante): cópia mais antiga') as motivo_rejeicao
from {{ ref('int_fator_avaliada') }}
where motivo_rejeicao is not null or ordem_duplicata > 1
