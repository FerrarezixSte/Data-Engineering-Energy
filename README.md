# Stephanni Ferrarezi · Portfólio de dados e energia

Site do portfólio (`index.html`) e um pipeline de dados aberto que alimenta os gráficos com **dados reais do ONS**.

## Pipeline ONS: GitHub Actions + DuckDB + dbt

```
ONS (bucket público, CC-BY 4.0)
   │  pipeline/ingest_ons.py      baixa os últimos 3 meses (Parquet) e padroniza as colunas
   ▼
data_lake/bronze/*.parquet        tudo como texto + arquivo de origem e horário de ingestão
   │  dbt build (dbt-duckdb)      tipos, regras de qualidade, quarentena e testes
   ▼
Silver  silver_geracao_usina · silver_restricao_eolica (+ tabelas *_rejeitados com o motivo)
Gold    gold_matriz_mensal · gold_geracao_diaria_usina · gold_perfil_horario · gold_restricao_eolica_mensal
   │  pipeline/export_site.py
   ▼
data/ons.json  ──►  gráficos do site (curva solar, eólica e restrição de operação)
   │  pipeline/previsao_solar.py  (+ clima do Open-Meteo, scikit-learn)
   ▼
data/previsao_solar.json  ──►  previsão de geração solar para amanhã
```

## IA: previsão de geração solar para amanhã (`pipeline/previsao_solar.py`)
- **Usina:** a solar com mais energia nos últimos 30 dias (dados do pipeline).
- **Clima:** Open-Meteo (gratuito, sem chave): irradiância global, direta e difusa, temperatura e nebulosidade dos últimos ~90 dias e a previsão de amanhã. Como o ONS não publica a localização na base horária, usa um ponto das regiões solares do estado; para uma usina específica, defina `SOLAR_LAT` e `SOLAR_LON`.
- **Modelo:** `HistGradientBoostingRegressor` (scikit-learn) com hora e época do ano.
- **Avaliação:** últimos 14 dias fora do treino, comparado com persistência (repetir ontem) e regressão linear só com irradiância. Métricas: MAE, RMSE e nMAE (erro em % da potência de referência).
- **Saída:** `data/previsao_solar.json` com as métricas, os últimos 7 dias (real × previsto) e a previsão hora a hora de amanhã.

Roda **todo dia às 06:17 (Brasília)** pelo GitHub Actions (`.github/workflows/pipeline-ons.yml`), grava o `data/ons.json` e publica o site. Custo: zero.

### Regras de qualidade (Silver)
| Regra | Geração por usina | Restrição eólica |
|---|---|---|
| R1 | instante válido | instante válido |
| R2 | usina identificada (CEG, id ONS ou nome) | usina identificada |
| R3 | geração numérica | geração presente e ≥ 0 |
| R4 | geração ≥ 0 | referência ≥ 0 |
| R5 | fonte conhecida | |
| Duplicata | (usina, instante) mantém a ingestão mais recente | idem |

### Testes dbt
`not_null` e `accepted_values` nas colunas-chave, mais testes de negócio:
Silver sem duplicatas · Bronze = Silver + quarentena · energia da Gold = Silver · participação da matriz soma 100%.

## Como publicar
1. Repositório público `FerrarezixSte.github.io` com estes arquivos na raiz.
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
