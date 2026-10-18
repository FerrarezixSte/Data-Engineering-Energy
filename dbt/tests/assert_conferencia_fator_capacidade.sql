-- Falha se Bronze de fator de capacidade != Silver + quarentena.
with b as (select count(*) n from {{ source('bronze', 'fator_capacidade') }}),
     s as (select count(*) n from {{ ref('silver_fator_capacidade') }}),
     r as (select count(*) n from {{ ref('silver_fator_capacidade_rejeitados') }})
select b.n as bronze, s.n as silver, r.n as rejeitados
from b, s, r
where b.n <> s.n + r.n
