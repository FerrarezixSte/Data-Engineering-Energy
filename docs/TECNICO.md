# Documentação técnica

Pipeline ELT diário com dados abertos do ONS: ingestão em Python, transformação e testes com dbt sobre DuckDB, orquestração no GitHub Actions e publicação estática no GitHub Pages. Custo de infraestrutura: zero.

← [Voltar ao README](../README.md) · [Site](https://ferrarezixste.github.io/Data-Engineering-Energy/)

---

## Sumário
1. [Arquitetura](#1-arquitetura)
2. [Fontes de dados](#2-fontes-de-dados)
3. [Bronze: ingestão](#3-bronze-ingestão)
4. [Silver: validação e quarentena](#4-silver-validação-e-quarentena)
5. [Gold: tabelas de consumo](#5-gold-tabelas-de-consumo)
6. [Testes de qualidade](#6-testes-de-qualidade)
7. [Publicação para o site](#7-publicação-para-o-site)
8. [Previsão de geração solar (ML)](#8-previsão-de-geração-solar-ml)
9. [Orquestração](#9-orquestração)
10. [Como rodar localmente](#10-como-rodar-localmente)
11. [Decisões e problemas encontrados](#11-decisões-e-problemas-encontrados)
12. [Limitações e próximos passos](#12-limitações-e-próximos-passos)

---

## 1. Arquitetura

```
ONS (S3 público, CC-BY 4.0)                     Open-Meteo (API, CC-BY 4.0)
        │                                                │
        ▼                                                │
 pipeline/ingest_ons.py ── Bronze: Parquet, tudo texto + auditoria
        │                                                │
        ▼                                                │
 dbt build (DuckDB) ── Silver: tipos, regras R1–R5, quarentena, deduplicação
        │              Gold: 5 tabelas de consumo
        │              23 testes (falha = não publica)
        ▼                                                ▼
 pipeline/export_site.py ──────────────────────► data/ons.json
 pipeline/previsao_solar.py ───────────────────► data/previsao_solar.json
        │                                         data/historico_previsao.json
        ▼
 commit em data/ → GitHub Pages (index.html lê os JSON no navegador)
```

| Camada | Onde | Formato | Responsabilidade |
|---|---|---|---|
| Raw | `data_lake/raw/` (cache do Actions) | arquivos do ONS | cópia fiel do download |
| Bronze | `data_lake/bronze/<conjunto>/` | Parquet, todas as colunas como texto | esquema canônico + `source_file` e `ingested_at` |
| Silver | `ons.duckdb` | tabelas dbt | tipagem, regras, quarentena, deduplicação |
| Gold | `ons.duckdb` | tabelas dbt | agregações prontas para consumo |
| Publicação | `data/*.json` | JSON | o que o site desenha |

**Por que DuckDB + dbt e não Spark ou um warehouse na nuvem:** o volume é de dezenas de milhões de linhas por trimestre, que um DuckDB processa em segundos dentro do runner do GitHub Actions. O dbt traz linhagem, testes e documentação sem custo. A mesma modelagem migra para Databricks, Snowflake ou BigQuery trocando o adapter.

## 2. Fontes de dados

| Conjunto (ONS) | Granularidade | Uso |
|---|---|---|
| Geração por usina em base horária | hora × usina | curvas, SCADA, matriz, previsão |
| Fator de capacidade (eólicas e solares) | hora × conjunto | capacidade instalada e coordenadas |
| Restrição de operação por *constrained-off* (eólicas) | 30 min × usina | energia cortada, gráfico eólico |
| Restrição de operação por *constrained-off* (fotovoltaicas) | 30 min × usina | alvo da previsão (geração disponível) |
| Open-Meteo (*forecast* + *past_days*) | hora × ponto | irradiância, temperatura, vento, nebulosidade |

## 3. Bronze: ingestão

`pipeline/ingest_ons.py`

- **Descoberta automática das pastas:** o script lista o bucket S3 público do ONS e escolhe a pasta de cada conjunto por palavras-chave. Se o ONS renomear uma pasta, o pipeline encontra a nova sem mudança de código e registra no log as candidatas.
- **Janela:** os últimos `ONS_MESES` meses (padrão 3). Os arquivos do mês atual e do anterior, que o ONS ainda revisa, são baixados de novo a cada execução; os mais antigos vêm do cache.
- **Esquema canônico:** cada conjunto tem um mapa de sinônimos de coluna (`din_instante`, `val_geracao`…) para nomes estáveis (`instante`, `geracao`…). Uma pasta sem as colunas obrigatórias é descartada, e o script tenta a próxima candidata.
- **Tudo como texto:** a Bronze não converte tipos. A conversão e as regras ficam na Silver, onde um valor ruim vai para a quarentena em vez de derrubar a ingestão.
- **Conjunto ausente:** gera uma Bronze vazia com o esquema certo, para o dbt continuar funcionando.

## 4. Silver: validação e quarentena

Cada fonte segue o padrão `int_<fonte>_avaliada` → `silver_<fonte>` (aceitas) + `silver_<fonte>_rejeitados` (quarentena com `motivo_rejeicao`).

**Regras da geração horária:**

| Regra | Rejeita quando |
|---|---|
| R1 | instante inválido |
| R2 | usina sem identificação (CEG, id ONS ou nome; marcadores como `-`, `0` ou `N/A` não contam, via macro `id_valido`) |
| R3 | geração não numérica |
| R4 | geração negativa |
| R5 | tipo de usina desconhecido |

**Deduplicação:** `row_number()` por (chave, nome da usina, instante), mantendo a ingestão mais recente. O nome entra na partição para que duas usinas com o mesmo identificador genérico nunca sejam tratadas como cópia uma da outra (ver a seção 11).

**Padronização:** tipos do ONS (`FOTOVOLTAICA`, `EOLIELÉTRICA`, `HIDROELÉTRICA`…) mapeados para `SOLAR`, `EOLICA`, `HIDRAULICA`, `TERMICA` e `NUCLEAR`. Na restrição, o intervalo de cada registro (30 ou 60 min) é inferido do próprio arquivo, e a energia restringida é `(referência − verificada) × horas`.

## 5. Gold: tabelas de consumo

| Tabela | Grão | Conteúdo |
|---|---|---|
| `gold_geracao_diaria_usina` | dia × usina | energia (MWh) e pico (MW) de solares e eólicas |
| `gold_perfil_horario` | hora × fonte × UF | curva típica do dia, por estado e total Brasil |
| `gold_matriz_mensal` | mês × fonte | energia (GWh) e participação (%) |
| `gold_fator_capacidade_mensal` | mês × conjunto | fator de capacidade médio |
| `gold_restricao_eolica_mensal` | mês × usina | energia eólica cortada pelo ONS |

## 6. Testes de qualidade

São 23 testes por `dbt build`. Os de severidade `error` bloqueiam a publicação; os de `warn` aparecem no log sem bloquear.

| Tipo | Testes |
|---|---|
| Integridade | `not_null` e `accepted_values` nas chaves, instantes, medidas e tipos de fonte |
| Conservação | **Bronze = Silver + quarentena** (geração e fator de capacidade): nenhuma linha some sem explicação |
| Consistência | energia da Gold = energia da Silver · participação da matriz soma 100% · Silver sem duplicatas |
| Física (`warn`) | usina solar não gera à noite (exceto MMGD, que é estimativa) |
| Identificador (`warn`) | uma chave de usina com mais de 3 nomes indica CEG genérico juntando usinas |
| Plausibilidade (`warn`) | mês completo com menos de 40 TWh ou eólica abaixo de 3% indica usinas perdidas |

## 7. Publicação para o site

`pipeline/export_site.py` lê a Gold e a Silver e grava `data/ons.json`:

- **Usinas fixas** (`pipeline/usinas.py`): Complexo Solar Janaúba (MG) e Complexo Eólico Lagoa dos Ventos (PI). Cada complexo é a soma das séries do ONS cujo nome contém o padrão. Se a usina sumir dos dados, o pipeline escolhe a maior usina física do período, sem usar o cadastro (coordenadas e capacidade) da usina configurada. As usinas podem ser trocadas por variável de ambiente (`USINA_SOLAR`, `USINA_EOLICA`).
- **Capacidade e coordenadas:** vêm do conjunto de fator de capacidade do ONS; na falta dele, do cadastro; em último caso, do pico observado + 5%.
- **Curvas:** a solar mostra o dia (0h às 24h); a eólica mostra a janela de 12h às 12h, com a madrugada no centro, junto com a referência e as horas restringidas pelo ONS.
- **SCADA:** replay do dia com a geração do ONS e o clima do Open-Meteo. A divisão por inversor ou aerogerador é estimada, porque o ONS só publica o total. Cada tag tem um código de qualidade: `GOOD` (medido), `EXT` (fonte externa) ou `EST` (estimado).
- **MMGD:** os agregados de micro e minigeração distribuída ficam fora de toda seleção de usina e aparecem numa seção própria.

## 8. Previsão de geração solar (ML)

`pipeline/previsao_solar.py`: previsão *day-ahead*, hora a hora, do Complexo Solar Janaúba.

**Modelo:** `HistGradientBoostingRegressor` (scikit-learn).

**Variáveis:**
- irradiância global, direta e difusa, com a da hora anterior e a da seguinte;
- temperatura e nebulosidade;
- hora do dia;
- seno da elevação solar (calculado, com equação do tempo);
- índice de céu limpo.

Não há variável de época do ano: com cerca de 90 dias de histórico, ela só serviria para o modelo decorar o período.

**Alinhamento temporal:** no Open-Meteo, a radiação das "07:00" é a média de 06:00 a 07:00. No ONS, o valor das "06:00" é a média de 06:00 a 07:00. Por isso o clima é deslocado −1 h. Sem isso, a geração ao amanhecer aparecia antes do sol.

**Alvo = geração disponível, não verificada.** Corte do ONS é uma decisão de operação, e o tempo não a explica:

| Situação | Tratamento |
|---|---|
| Hora com restrição publicada pelo ONS | alvo = verificada + MW cortados |
| Patamar: ≥ 3 h com potência parada (< 1% da capacidade) e sol variando | fora do treino e da avaliação |
| Queda sem registro: real < 50% do esperado por ≥ 3 h | fora do treino e da avaliação; o "esperado" vem de `cross_val_predict` com `GroupKFold` por dia, para o modelo não julgar dias que já viu |

**Avaliação:**
- Treino com cerca de 90 dias e teste nos últimos 14, fora do treino.
- Comparação com a **persistência** (mesma hora do dia anterior) e com a **regressão linear** só com irradiância.
- Métrica: **nMAE** (MAE ÷ capacidade de referência), publicada em três versões:
  1. horas sem corte, com o tempo observado (compara modelos);
  2. todas as horas, com o tempo observado;
  3. **erro real do dia seguinte:** a previsão publicada na véspera, com o tempo ainda previsto, contra o que a usina gerou. Fica guardada em `data/historico_previsao.json`. É a métrica que reflete o uso real.

**Validação do tratamento de cortes:** num conjunto sintético com cortes não publicados, em que a geração disponível verdadeira é conhecida, o erro contra ela caiu de 11,4% para 3,7%, e foram detectadas 96 de cerca de 113 horas cortadas.

## 9. Orquestração

`.github/workflows/pipeline-ons.yml`

| Gatilho | O que roda |
|---|---|
| `schedule` (todo dia às 06:17, horário de Brasília) | pipeline completo + deploy |
| `workflow_dispatch` (botão "Run workflow") | pipeline completo + deploy |
| `push` na `main` | só o deploy do site |

Etapas do pipeline: Bronze → `dbt build` → export → previsão (`continue-on-error`, para um problema na IA não travar os outros gráficos) → commit em `data/` → deploy no Pages. O cache do Actions guarda os arquivos brutos do ONS entre execuções, e `concurrency` impede duas execuções ao mesmo tempo.

## 10. Como rodar localmente

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r pipeline/requirements.txt

python pipeline/ingest_ons.py                          # Bronze (ONS_MESES=1 para testar mais rápido)
dbt build --project-dir dbt --profiles-dir dbt         # Silver + Gold + testes
python pipeline/export_site.py                         # data/ons.json
python pipeline/previsao_solar.py                      # data/previsao_solar.json

python -m http.server 8000                             # abrir http://localhost:8000
```

| Variável | Padrão | Para quê |
|---|---|---|
| `ONS_MESES` | 3 | meses de histórico baixados |
| `USINA_SOLAR` / `USINA_EOLICA` | Janaúba / Lagoa dos Ventos | trecho do nome da usina no ONS (sem acento) |
| `USINA_SOLAR_LAT`, `_LON`, `_CAPACIDADE_MW` | do ONS | forçar cadastro |
| `SOLAR_LAT` / `SOLAR_LON` | localização da usina | forçar o ponto do clima da previsão |
| `PREVISAO_CLIMA_ARQUIVO` | – | usar um JSON local do Open-Meteo (testes offline) |
| `SEM_CLIMA` | – | exportar sem consultar o Open-Meteo |

**Estrutura:**
```
pipeline/   ingest_ons.py · usinas.py · export_site.py · previsao_solar.py
dbt/        models/silver · models/gold · tests · macros
data/       ons.json · previsao_solar.json · historico_previsao.json   (gerados pelo pipeline)
docs/       TECNICO.md
index.html  site (HTML, CSS e JS sem framework, gráficos em <canvas>)
```

## 11. Decisões e problemas encontrados

- **Identificador genérico do ONS.** Usinas agrupadas vinham com CEG `-`. A chave `coalesce(ceg, id_ons, nome)` aceitava o `-` como identificador, então a deduplicação via milhares de usinas como uma só e descartava quase tudo. Sintomas: eólica com 0,5% da matriz, total de 36 TWh no mês e um agregado de MMGD "gerando" à noite. A correção (macro `id_valido` + nome na partição) levou a matriz para cerca de 60 TWh, com eólica perto de 19%.
- **MMGD não é usina.** O ONS publica a micro e minigeração distribuída como uma "usina" por estado, com valores estimados. Ela foi separada de toda seleção de usina e do teste físico.
- **Convenção de horário entre fontes.** O deslocamento de 1 h entre ONS e Open-Meteo foi identificado pelas bordas do dia: geração positiva antes de existir irradiância no rótulo correspondente.
- **Cortes do ONS na previsão.** Treinar com a geração verificada ensinava o modelo a prever cortes como se fossem tempo ruim. Isso foi resolvido usando a geração disponível como alvo e excluindo cortes não publicados.
- **Testes como aviso ou como erro.** Regras de integridade e conservação bloqueiam a publicação. Regras de plausibilidade só avisam, porque o ONS publica ruído legítimo e um alarme falso não deve derrubar o site.

## 12. Limitações e próximos passos

- **Cortes futuros não são previsíveis pelo tempo.** O erro real do dia seguinte inclui as ordens do ONS do próprio dia, então nunca vai zerar.
- **Histórico curto (cerca de 90 dias).** Limita o que o modelo aprende sobre as estações do ano.
- **Próximos passos:**
  - modelo híbrido: física com `pvlib` (rastreadores, temperatura dos módulos) + gradient boosting no resíduo;
  - média de vários modelos meteorológicos (ECMWF, GFS, ICON) do Open-Meteo;
  - previsão probabilística por regressão quantílica;
  - incremental no dbt para ampliar o histórico sem reprocessar tudo.
