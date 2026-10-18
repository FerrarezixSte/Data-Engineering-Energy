-- Falha se Bronze != Silver + quarentena (nenhuma linha pode sumir no caminho).
with b as (select count(*) n from {{ source('bronze', 'geracao_usina') }}),
     s as (select count(*) n from {{ ref('silver_geracao_usina') }}),
     r as (select count(*) n from {{ ref('silver_geracao_usina_rejeitados') }})
select b.n as bronze, s.n as silver, r.n as rejeitados
from b, s, r
where b.n <> s.n + r.n
