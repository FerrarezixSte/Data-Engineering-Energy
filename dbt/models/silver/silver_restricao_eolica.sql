-- Silver: restrição de operação de eólicas. Energia restringida = referência - verificada nos instantes com restrição.
with v as (
    select * from {{ ref('int_restricao_avaliada') }}
    where motivo_rejeicao is null and ordem_duplicata = 1
),
intervalo as (  -- o conjunto vem em base semi-horária; confirma pelos minutos dos instantes
    select case when max(minute(instante)) > 0 then 0.5 else 1.0 end as horas from v
)
select
    instante, year(instante) as ano, month(instante) as mes,
    chave_usina, usina, estado,
    geracao_mw, referencia_mw, limitada_mw, razao_restricao,
    (razao_restricao is not null) as com_restricao,
    case when razao_restricao is not null then greatest(coalesce(referencia_mw, geracao_mw) - geracao_mw, 0) else 0 end as restringido_mw,
    cast(intervalo.horas as double) as horas_intervalo,
    source_file, ingested_at
from v cross join intervalo
