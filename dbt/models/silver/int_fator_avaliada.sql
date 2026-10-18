{{ config(materialized='view') }}
with tipada as (
    select
        coalesce(try_cast(instante as timestamp),
                 try_strptime(instante, '%d/%m/%Y %H:%M:%S'),
                 try_strptime(instante, '%d/%m/%Y %H:%M'))                       as instante,
        nullif(trim(conjunto), '')                                               as conjunto,
        coalesce(nullif(trim(ceg), ''), nullif(trim(conjunto), ''))             as chave_conjunto,
        upper(nullif(trim(estado), ''))                                          as estado,
        case
            when strip_accents(upper(tipo_usina)) like '%FOTOV%'
              or strip_accents(upper(tipo_usina)) like '%SOLAR%' then 'SOLAR'
            when strip_accents(upper(tipo_usina)) like '%EOL%'  then 'EOLICA'
        end                                                                      as tipo_fonte,
        try_cast(replace(trim(capacidade), ',', '.') as double)                  as capacidade_mw,
        try_cast(replace(trim(geracao), ',', '.') as double)                     as geracao_verificada_mw,
        try_cast(replace(trim(programada), ',', '.') as double)                  as geracao_programada_mw,
        try_cast(replace(trim(fator), ',', '.') as double)                       as fator_bruto,
        try_cast(replace(trim(lat), ',', '.') as double)                         as latitude,
        try_cast(replace(trim(lon), ',', '.') as double)                         as longitude,
        source_file,
        try_cast(ingested_at as timestamp)                                       as ingested_at
    from {{ source('bronze', 'fator_capacidade') }}
),
com_fator as (
    select *,
        -- usa o fator informado pelo ONS; se faltar, calcula. Alguns arquivos trazem em % (0-100).
        case
            when fator_bruto is null then geracao_verificada_mw / nullif(capacidade_mw, 0)
            when fator_bruto > 1.5   then fator_bruto / 100
            else fator_bruto
        end as fator_capacidade
    from tipada
),
avaliada as (
    select *,
        nullif(concat_ws(' | ',
            case when instante is null                                   then 'R1: instante inválido' end,
            case when chave_conjunto is null                             then 'R2: usina/conjunto sem identificação' end,
            case when capacidade_mw is null or capacidade_mw <= 0        then 'R3: capacidade instalada ausente ou zero' end,
            case when fator_capacidade is null or fator_capacidade < 0
                   or fator_capacidade > 1.2                             then 'R4: fator de capacidade fora de 0 a 1,2' end,
            case when tipo_fonte is null                                 then 'R5: fonte não é solar nem eólica' end
        ), '') as motivo_rejeicao
    from com_fator
)
select *,
    case when motivo_rejeicao is null
         then row_number() over (partition by chave_conjunto, instante order by ingested_at desc, source_file desc)
    end as ordem_duplicata
from avaliada
