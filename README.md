# Stephanni Ferrarezi · Portfólio de dados e energia

Site do portfólio (`index.html`) e um pipeline de dados aberto que alimenta os gráficos com **dados reais do ONS**.

## Pipeline ONS: GitHub Actions + DuckDB + dbt

```
ONS (bucket público, CC-BY 4.0)
   │  pipeline/ingest_ons.py      baixa os últimos 3 meses de 3 conjuntos (Parquet) e padroniza as colunas
   ▼
data_lake/bronze/*.parquet        tudo como texto + arquivo de origem e horário de ingestão
   │  dbt build (dbt-duckdb)      tipos, regras de qualidade, quarentena e testes
   ▼
Silver  silver_geracao_usina · silver_fator_capacidade · silver_restricao_eolica (+ tabelas *_rejeitados com o motivo)
Gold    gold_matriz_mensal · gold_geracao_diaria_usina · gold_perfil_horario · gold_fator_capacidade_mensal · gold_restricao_eolica_mensal
   │  pipeline/export_site.py
   ▼
data/ons.json  ──►  curvas solar e eólica (mesmo dia), replay do SCADA, MMGD, restrição de operação e matriz
   │  pipeline/previsao_solar.py  (+ clima do Open-Meteo, scikit-learn)
   ▼
data/previsao_solar.json  ──►  previsão de geração solar para amanhã
```

## Usinas do site (`pipeline/usinas.py`)
Curvas do topo, painel SCADA e previsão usam as mesmas duas usinas reais, publicadas pelo ONS em base horária:

| Fonte | Usina | Onde |
|---|---|---|
| Solar | Complexo Solar São Gonçalo (Enel Green Power, 864 MW) | São Gonçalo do Gurguéia, PI |
| Eólica | Complexo Eólico Lagoa dos Ventos (Enel Green Power) | Piauí |

- O ONS publica cada complexo em várias séries; o pipeline soma todas as que têm o trecho do nome (`SAO GONCALO`, `LAGOA DOS VENTOS`).
- **Capacidade e coordenadas** vêm do conjunto "fator de capacidade" do ONS; se faltar, do cadastro em `usinas.py`; em último caso, pico observado + 5%.
- **Trocar de usina sem mexer no código:** `USINA_SOLAR` / `USINA_EOLICA` com um trecho do nome no ONS (sem acento) e, se quiser, `USINA_SOLAR_LAT`, `USINA_SOLAR_LON`, `USINA_SOLAR_CAPACIDADE_MW` (idem para `USINA_EOLICA_*`).
- **Se a usina sumir dos dados**, o pipeline não quebra: usa a maior usina física do período e o site avisa.
- **Fora da escolha:** os agregados de **MMGD** (micro e minigeração distribuída, estimados pelo ONS por estado, com patamar noturno) e qualquer série solar que gere à noite. A MMGD tem uma seção própria no site.

## SCADA com replay de um dia real
O painel reproduz, a cada 10 minutos simulados, o dia publicado em `data/ons.json`: potência total verificada pelo ONS (qualidade `GOOD`), irradiância, temperatura e vento a 100 m do Open-Meteo (`EXT`). O ONS não publica dados por inversor ou aerogerador, então os 8 blocos/grupos, as tags internas e os eventos são estimados a partir do total (`EST`). Sem `data/ons.json`, o painel volta para a simulação ilustrativa.

## IA: previsão de geração solar para amanhã (`pipeline/previsao_solar.py`)
- **Usina:** o Complexo Solar São Gonçalo, a mesma do SCADA (ver acima).
- **Clima:** Open-Meteo (gratuito, sem chave) no local da usina: irradiância global, direta e difusa, temperatura e nebulosidade dos últimos ~90 dias e a previsão de amanhã. `SOLAR_LAT` e `SOLAR_LON` continuam valendo para forçar outro ponto.
- **Modelo:** `HistGradientBoostingRegressor` (scikit-learn) com hora e época do ano.
- **Avaliação:** últimos 14 dias fora do treino, comparado com persistência (repetir ontem) e regressão linear só com irradiância. Métricas: MAE, RMSE e nMAE (erro em % da potência de referência).
- **Saída:** `data/previsao_solar.json` com as métricas, os últimos 7 dias (real × previsto) e a previsão hora a hora de amanhã. Se a persistência errar menos que o modelo, o site mostra isso como está ("mais erro que a persistência"), com uma nota explicando.

Roda **todo dia às 06:17 (Brasília)** pelo GitHub Actions (`.github/workflows/pipeline-ons.yml`), grava o `data/ons.json` e publica o site. Custo: zero.

### Regras de qualidade (Silver)
| Regra | Geração por usina | Restrição eólica |
|---|---|---|
| R1 | instante válido | instante válido |
| R2 | usina identificada (CEG, id ONS ou nome; marcadores como `-` ou `N/A` não contam como identificador) | usina identificada |
| R3 | geração numérica | geração presente e ≥ 0 |
| R4 | geração ≥ 0 | referência ≥ 0 |
| R5 | fonte conhecida | |

**Fator de capacidade:** instante válido · conjunto identificado · capacidade > 0 · fator entre 0 e 1,2 (valores em % são convertidos) · fonte solar ou eólica · sem duplicatas.

**Duplicatas:** por (chave, nome da usina, instante), mantendo a ingestão mais recente. O nome entra na chave para que duas usinas com o mesmo identificador genérico nunca sejam tratadas como cópia uma da outra.

### Testes dbt
`not_null` e `accepted_values` nas colunas-chave, mais testes de negócio:
Silver sem duplicatas · Bronze = Silver + quarentena (geração e fator de capacidade) · energia da Gold = Silver · participação da matriz soma 100%.

Avisos de plausibilidade (`severity: warn`, não bloqueiam a publicação):
- **Física:** usina solar não gera à noite (fora a MMGD).
- **Identificador:** uma chave de usina com mais de 3 nomes indica CEG genérico juntando usinas diferentes.
- **Matriz:** mês completo com menos de 40 TWh ou eólica abaixo de 3% indica usinas se perdendo na ingestão.

Total: 21 testes por execução (o `dbt build` também conta os 14 modelos).

## Como publicar
1. Repositório público `data-engineering-energy` com estes arquivos na raiz.
   Site: https://ferrarezixste.github.io/data-engineering-energy/
2. **Settings → Pages → Source: GitHub Actions.**
3. **Settings → Actions → General → Workflow permissions: Read and write.**
4. **Actions → Pipeline ONS e site → Run workflow** para a primeira carga. Depois roda sozinho todo dia.

## Rodar localmente
```bash
pip install -r pipeline/requirements.txt
python pipeline/ingest_ons.py
dbt build --project-dir dbt --profiles-dir dbt
python pipeline/export_site.py
python pipeline/previsao_solar.py
python -m http.server   # abra http://localhost:8000
```
Variável opcional: `ONS_MESES` (padrão 3).

Fonte dos dados: [ONS - Dados Abertos](https://dados.ons.org.br), licença CC-BY 4.0.
