# Pipeline de dados do setor elétrico

**Do dado público do ONS ao dashboard, todo dia, sem ninguém apertar um botão.**

🔗 **Ver o projeto funcionando:** [ferrarezixste.github.io/Data-Engineering-Energy](https://ferrarezixste.github.io/Data-Engineering-Energy/)
👩‍💻 **Autora:** Stephanni Ferrarezi · [LinkedIn](https://www.linkedin.com/in/stephanni-ferrarezi) · dataengineer.ste@gmail.com

---

## Em uma frase

Todo dia, às 6h da manhã, este projeto baixa sozinho os dados abertos do **ONS** (o Operador Nacional do Sistema Elétrico), confere a qualidade de cada registro, organiza tudo em tabelas prontas para análise e atualiza um site com gráficos, um painel de operação de usinas e uma previsão de geração solar feita com inteligência artificial.

## O que tem no site

| | O que mostra |
|---|---|
| ☀️ 🌬️ **Geração real** | Quanto uma usina solar e um parque eólico geraram, hora a hora, com dados oficiais do ONS |
| 🖥️ **Painel SCADA** | Reproduz um dia real de duas usinas, como a tela que um operador acompanha numa sala de controle |
| ✂️ **Energia cortada** | Quanto os parques eólicos deixaram de gerar porque o ONS mandou reduzir (o chamado *constrained-off*) |
| 🏠 **Geração nos telhados** | A energia dos painéis solares de casas e comércios, que o ONS estima por estado |
| 📊 **Matriz elétrica** | A participação de cada fonte (hidráulica, eólica, solar, térmica e nuclear) na energia do Brasil |
| 🤖 **Previsão com IA** | Um modelo de *machine learning* que prevê a geração solar do dia seguinte usando a previsão do tempo |

As usinas são reais e qualquer pessoa pode conferir os números no site do ONS: o **Complexo Solar Janaúba** (MG), o maior do Brasil, e o **Complexo Eólico Lagoa dos Ventos** (PI).

## Por que este projeto é diferente

- **Roda sozinho e não custa nada.** Usa só ferramentas gratuitas: GitHub Actions para agendar, GitHub Pages para publicar e programas *open source* para processar.
- **Confere a qualidade dos dados antes de publicar.** São 23 verificações automáticas a cada execução. Se alguma falhar, o site não é atualizado, e ninguém vê número errado.
- **Usa conhecimento do setor elétrico para achar erros.** Uma das regras é simples para quem trabalha com energia: usina solar não gera à noite. Foi uma regra desse tipo que revelou um problema nos dados do ONS. Usinas diferentes eram tratadas como se fossem a mesma, e a energia eólica aparecia com **0,5% da matriz, quando o certo é 19%**. Depois da correção, o total do mês passou de 36 para 60 TWh, em linha com a operação real.
- **É honesto com os próprios erros.** A previsão de IA publica o erro real do dia anterior e se compara com a regra mais simples possível ("amanhã vai ser igual a hoje"). Quando o modelo perde, o site mostra que perdeu.

## Competências que o projeto demonstra

**Engenharia de dados:** pipelines automatizados (ETL/ELT), arquitetura em camadas (medalhão), modelagem de dados, testes e qualidade de dados, orquestração e versionamento.

**Análise e ciência de dados:** SQL, Python, *machine learning* para séries temporais, avaliação de modelos e visualização de dados.

**Setor elétrico:** geração solar e eólica, operação do sistema elétrico (SIN), restrições do ONS, SCADA e fator de capacidade.

**Ferramentas:** Python · SQL · dbt · DuckDB · GitHub Actions · scikit-learn · JavaScript · Git

## Sobre a autora

Stephanni tem **8 anos no setor elétrico**, entre usinas solares, subestações e obras de média tensão, coordenando orçamento, cronograma e equipes de campo de mais de 60 pessoas. Hoje une essa experiência de campo à engenharia de dados: as regras de qualidade do projeto vêm de como a energia se comporta de verdade.

---

<sub>📘 Para os detalhes técnicos (arquitetura, modelos de dados, testes, previsão e como rodar localmente), veja a [documentação técnica](docs/TECNICO.md).
Dados: [ONS · Dados Abertos](https://dados.ons.org.br/) e [Open-Meteo](https://open-meteo.com/), licença CC-BY 4.0.</sub>
