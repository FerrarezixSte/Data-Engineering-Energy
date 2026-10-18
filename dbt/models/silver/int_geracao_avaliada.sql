-- materializada como tabela: Silver e quarentena leem exatamente a mesma avaliação
{{ config(materialized='table') }}
-- Converte tipos e avalia cada linha da Bronze contra as regras técnicas.
-- Linhas com motivo_rejeicao vão para a quarentena; as demais seguem para a Silver.
with tipada as (
    select
        coalesce(try_cast(instante as timestamp),
                 try_strptime(instante, '%d/%m/%Y %H:%M:%S'),
                 try_strptime(instante, '%d/%m/%Y %H:%M'))                       as instante,
        nullif(trim(usina), '')                                                  as usina,
        coalesce(nullif(trim(ceg), ''), nullif(trim(id_ons), ''), nullif(trim(usina), '')) as chave_usina,
        upper(nullif(trim(estado), ''))                                          as estado,
        nullif(trim(subsistema), '')                                             as subsistema,
        case
            when strip_accents(upper(tipo_usina)) like '%FOTOV%'
              or strip_accents(upper(tipo_usina)) like '%SOLAR%' then 'SOLAR'
            when strip_accents(upper(tipo_usina)) like '%EOL%'  then 'EOLICA'
            when strip_accents(upper(tipo_usina)) like '%HIDR%' then 'HIDRAULICA'
            when strip_accents(upper(tipo_usina)) like '%NUCLE%' then 'NUCLEAR'
            when strip_accents(upper(tipo_usina)) like '%TERM%' then 'TERMICA'
        end                                                                      as tipo_fonte,
        try_cast(replace(trim(geracao), ',', '.') as double)                     as geracao_mw,
        source_file,
        try_cast(ingested_at as timestamp)                                       as ingested_at
    from {{ source('bronze', 'geracao_usina') }}
),
avaliada as (
    select *,
        nullif(concat_ws(' | ',
            case when instante is null    then 'R1: instante inválido' end,
            case when chave_usina is null then 'R2: usina sem identificação' end,
            case when geracao_mw is null  then 'R3: geração não numérica' end,
            case when geracao_mw < 0      then 'R4: geração negativa' end,
            case when tipo_fonte is null  then 'R5: tipo de usina desconhecido' end
        ), '') as motivo_rejeicao
    from tipada
)
select *,
    -- a numeração considera só as linhas válidas; desempate fixo para o resultado ser sempre o mesmo
    case when motivo_rejeicao is null
         then row_number() over (partition by chave_usina, instante, (motivo_rejeicao is null)
                                   order by ingested_at desc, source_file desc, geracao_mw desc nulls last)
    end as ordem_duplicata
from avaliada
