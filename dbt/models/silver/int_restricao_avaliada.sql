{{ config(materialized='view') }}
with tipada as (
    select
        coalesce(try_cast(instante as timestamp),
                 try_strptime(instante, '%d/%m/%Y %H:%M:%S'),
                 try_strptime(instante, '%d/%m/%Y %H:%M'))                       as instante,
        nullif(trim(usina), '')                                                  as usina,
        coalesce(nullif(trim(ceg), ''), nullif(trim(id_ons), ''), nullif(trim(usina), '')) as chave_usina,
        upper(nullif(trim(estado), ''))                                          as estado,
        try_cast(replace(trim(geracao), ',', '.') as double)                     as geracao_mw,
        try_cast(replace(trim(referencia), ',', '.') as double)                  as referencia_mw,
        try_cast(replace(trim(limitada), ',', '.') as double)                    as limitada_mw,
        nullif(trim(razao), '')                                                  as razao_restricao,
        source_file,
        try_cast(ingested_at as timestamp)                                       as ingested_at
    from {{ source('bronze', 'restricao_eolica') }}
),
avaliada as (
    select *,
        nullif(concat_ws(' | ',
            case when instante is null                       then 'R1: instante inválido' end,
            case when chave_usina is null                    then 'R2: usina sem identificação' end,
            case when geracao_mw is null or geracao_mw < 0   then 'R3: geração ausente ou negativa' end,
            case when referencia_mw < 0                      then 'R4: referência negativa' end
        ), '') as motivo_rejeicao
    from tipada
)
select *,
    case when motivo_rejeicao is null
         then row_number() over (partition by chave_usina, instante order by ingested_at desc, source_file desc)
    end as ordem_duplicata
from avaliada
