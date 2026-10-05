# Monitoramento das Eleições 2026 — Presidência, Governos e Ceará

Painel HTML **ao vivo** da apuração oficial do TSE para as Eleições 2026, servido por HTTP
e atualizado sozinho. Mostra a corrida presidencial, cinco governos estaduais, o mapa do
Brasil pintado por lado político e situação de turno, e as estatísticas dos cargos
proporcionais do Ceará (senador, deputado federal e deputado estadual).

Os dados vêm **exclusivamente** do feed público do TSE — nada é estimado, interpolado ou
inventado. Quando um número não está disponível, o painel mostra isso.

## O que o painel mostra

**Aba 1 — País, Ceará e Estados**
- Presidente: os dois primeiros colocados, votos, percentual e apuração por bloco de regiões
  (Sul + Sudeste + Centro-Oeste, Nordeste, Norte).
- Governos de **Ceará, São Paulo, Rio Grande do Sul, Paraíba e Rio de Janeiro**: dois
  candidatos cada, empilhados na coluna lateral.

**Aba 2 — Mapa dos governadores**
- Mapa do Brasil em SVG com cada estado pintado por **lado político** (vermelho = esquerda,
  verde = direita) e por **situação** (tom cheio = eleito no 1º turno, tom claro = vai ao 2º turno).
- Marcador com **foto e percentual** do governador projetado em cada estado.
- Lista dos já eleitos agrupada por região, com zoom no mapa (roda do mouse, arrastar e barra).

**Aba 3 — Ceará: Senado e Câmara**
- Senador, deputado federal e deputado estadual: os mais votados, a votação somada por partido,
  o quociente eleitoral e a **projeção de quem se elege**, com selo nos candidatos projetados.

## Como funciona

Quatro camadas, cada uma num arquivo:

```
painel_eleicao.py      coleta no TSE, normaliza, compara e (opcionalmente) desenha um PNG
estatisticas_ce.py     coleta e projeta os cargos proporcionais do Ceará
mapa_governadores.py   gera o SVG pintado do mapa
painel_html.py         monta o HTML das abas (e o JSON com os dados)
servidor_eleicao.py    serve tudo em HTTP e regenera num laço
```

O servidor regenera a cada 60 segundos e publica três rotas:

| Rota | Conteúdo |
|---|---|
| `/` | a página completa (HTML + CSS + JS) |
| `/dados.json` | os valores numéricos para o refresh |
| `/mapa.svg` | o mapa pintado, como arquivo separado |
| `/painel.png` | imagem estática de reserva |

**O navegador não recarrega a página.** A cada 60 segundos o JavaScript busca apenas
`/dados.json` e reescreve os valores na tela. Assim a aba aberta, o zoom do mapa e a
posição de leitura ficam parados — só os números mudam. O HTML tem ~80 KB e o JSON ~60 KB
(as fotos dos candidatos dos cargos proporcionais são o peso maior dele).

## De onde vêm os dados

Feed público do TSE, em `https://resultados.tse.jus.br/oficial/ele2026`:

```
.../dados/<uf>/<uf>-c<cargo>-e<eleicao>-u.json     resultado por UF
.../fotos/<uf>/<sequencial>.jpeg                   foto oficial do candidato
```

Códigos usados (conferidos na fonte, **não chutados**):

| Cargo | Código | Eleição |
|---|---|---|
| Presidente | `0001` | `6257` |
| Governador | `0003` | `6259` |
| Senador | `0005` | `6259` |
| Deputado Federal | `0006` | `6259` |
| Deputado Estadual | `0007` | `6259` |

O arquivo de cada cargo traz `carg[0].nv` com o **número de vagas** (senador 2, federal 22,
estadual 46 no Ceará) e `carg[0].fed` com as **federações** daquele pleito.

> **Cuidado:** requisições repetidas a URLs inexistentes no TSE bloqueiam o IP. Nunca
> teste URL por tentativa e erro — descubra o código antes, na documentação ou num arquivo
> que já responde.

## Como rodar

Requer Python 3 com **Pillow** (`PIL`) para as fotos e o PNG. O resto é biblioteca padrão.

```bash
# 1. coloque os assets onde o código espera
mkdir -p ~/.hermes/eleicao
cp dados/br.svg dados/historico_2022.json ~/.hermes/eleicao/

# 2. suba o servidor (porta opcional, padrão 8090)
python servidor_eleicao.py 8090

# 3. abra
#    http://localhost:8090/
```

Para expor na internet sem abrir porta, um túnel Cloudflare serve:

```bash
cloudflared tunnel --url http://localhost:8090
```

Para ver o resultado uma vez só, sem servidor:

```bash
python painel_eleicao.py        # desenha o PNG
python estatisticas_ce.py       # imprime os números do Ceará no terminal
python painel_html.py /tmp/p.html   # escreve o HTML num arquivo
```

## Como a projeção de cadeiras é calculada

**Senador é majoritário:** as vagas vão para os mais votados. Sem quociente.

**Deputado federal e estadual são proporcionais.** Para cada cargo o cálculo é:

1. **Quociente eleitoral** = votos válidos ÷ número de vagas.
2. Os votos são somados **por federação** (que concorre junta) ou por partido isolado
   quando não há federação.
3. Só concorre a vaga quem tem candidato acima do **piso de 10% do quociente**.
4. As vagas são distribuídas uma a uma pela **maior média** (`votos do grupo ÷ (cadeiras + 1)`),
   sempre entre os grupos que ainda têm candidato apto a preencher a vaga.

O resultado muda enquanto a apuração corre: o quociente depende do total de votos válidos e
a ordem dentro de cada grupo depende de quem ainda não foi apurado. Nada disso é previsão
estatística — é a **regra eleitoral aplicada aos votos já apurados**, e está rotulado como
projeção na tela.

## Limites conhecidos

- **Rio Grande do Norte não existe no `br.svg`** usado como base: fica sem cor e sem marcador.
- A classificação **esquerda/direita é política, não oficial do TSE** — está avisada na tela.
- A apuração em curso não diz **quem ocupou a cadeira** no proporcional; por isso a projeção
  é rotulada como tal.
- Marcadores se encostam quando os estados são pequenos e vizinhos (SP/MG/RJ, bloco do
  Nordeste). O zoom resolve em parte.
- A URL de um túnel Cloudflare **muda quando o processo reinicia**.
- A comparação com 2022 existe no código (`HIST2022`), mas está desligada na renderização.

## Aviso

Projeto pessoal, sem vínculo com o TSE. A fonte é pública e oficial; eventuais erros de
leitura, classificação política ou projeção são do autor, não do TSE.
