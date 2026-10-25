{# Devolve o identificador limpo, ou null quando ele é só um marcador ("-", "0", "N/A", "NULL"...).
   Sem isso, todas as linhas com CEG "-" viram a mesma "usina" e a deduplicação descarta quase todas. #}
{% macro id_valido(coluna) -%}
    case
        when {{ coluna }} is null then null
        when regexp_matches(trim({{ coluna }}), '^[-0./ ]*$') then null
        when upper(trim({{ coluna }})) in ('NA', 'N/A', 'N.A.', 'NULL', 'NONE', 'NAN', 'SEM CEG') then null
        else trim({{ coluna }})
    end
{%- endmacro %}
