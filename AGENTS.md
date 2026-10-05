# AGENTS.md

Instruções para agentes de IA (ou pessoas) que forem mexer neste repositório.

## 1. Regra de ouro: nunca chutar URL do TSE

Requisições repetidas a URLs inexistentes em `resultados.tse.jus.br` **bloqueiam o IP** —
e derrubam o painel que está no ar. Para descobrir um caminho novo:

- procure o código na documentação oficial ou em busca web **antes** de tentar;
- ou tire o dado de um arquivo que **já responde** (o `carg` de um JSON conhecido traz
  cargos, vagas e federações);
- se precisar sondar, sonde **uma ou duas** URLs, nunca uma lista.

O padrão que funciona hoje:

```
https://resultados.tse.jus.br/oficial/ele2026/<eleicao>/dados/<uf>/<uf>-c<cargo>-e<eleicao>-u.json
```

com os pares cargo/eleição documentados no `README.md`. A mesma eleição (`6259`) serve
senador, deputado federal e estadual do estado — só o cargo muda.

## 2. O refresh é o contrato mais importante do projeto

O navegador **nunca recarrega a página**. A cada 60 s o front busca apenas `/dados.json` e
reescreve os valores. Isso existe para que a aba aberta, o zoom do mapa e a posição de
leitura não se movam.

Consequências ao mexer no código:

- **Não** coloque dado que muda a cada ciclo dentro do HTML se ele puder vir no JSON —
  senão a tela congela com número velho.
- Quando um bloco é reordenável (ranking, lista de eleitos), o JSON precisa carregar também
  **a ordem e a identidade** das linhas, não só os números, porque o JS remonta a lista.
- Fotos vão no JSON apenas quando o bloco precisa se remontar sozinho. Elas são o maior peso
  do arquivo — hoje ~60 KB por causa das 24 fotos dos proporcionais. Se puder evitá-las, evite.
- Todo elemento atualizável carrega `data-c="caminho.no.json"` e `data-f="pct|num"`; o
  atualizador genérico cuida do resto. Use isso em vez de escrever JS novo.

## 3. Onde cada coisa mora

| Arquivo | Papel |
|---|---|
| `painel_eleicao.py` | coleta e normaliza o TSE; define `CORRIDAS`, `REGIOES`, cores e fontes |
| `estatisticas_ce.py` | cargos proporcionais do Ceará e a projeção de cadeiras |
| `mapa_governadores.py` | monta o SVG pintado a partir do `br.svg` |
| `painel_html.py` | HTML das abas, CSS, JS e o JSON (`dados()`) |
| `servidor_eleicao.py` | HTTP, laço de regeneração e as rotas |

Caminhos absolutos são esperados: `~/.hermes/eleicao/` (assets e fotos) e
`~/.hermes/eleicao/br.svg` (base do mapa). O repositório guarda cópias em `dados/`.

## 4. Convenções

- **Português** em comentários, nomes de domínio e mensagens de tela. Nomes de função em
  português quando o domínio é eleitoral (`pega_resultado`, `candidatos`, `coleta`).
- Cada módulo tem `if __name__ == "__main__"` que imprime o resultado no terminal — é o
  teste mais rápido de todos. Rode antes de subir.
- Os módulos se importam por `sys.path.insert(0, Path(__file__).parent)`, então **rodam do
  mesmo diretório**. Não transforme em pacote sem ajustar isso.
- Percentual é `float`; no HTML quem formata é o JS (`nf`/`nf2`). Não formate no Python o que
  o JS vai reescrever.

## 5. Antes de dar por pronto

```bash
python3 -c "import ast; [ast.parse(open(f).read()) for f in ['painel_eleicao.py',
 'painel_html.py','mapa_governadores.py','estatisticas_ce.py','servidor_eleicao.py']]"
python estatisticas_ce.py                 # confere os números do Ceará
curl -s http://localhost:8090/dados.json | head -c 300
curl -o /dev/null -w '%{http_code}\n' http://localhost:8090/     # espera 200
```

Ao reiniciar o servidor, **mate pelo PID**:

```bash
PID=$(pgrep -f 'servidor_eleicao.py' | head -1); [ -n "$PID" ] && kill $PID
```

`pkill -f` já deu falso positivo neste ambiente.

## 6. Segredos

**Nada de credencial neste repositório.** O código lê o que precisar de variáveis de
ambiente (`TELEGRAM_BOT_TOKEN` é lido por nome, nunca embutido). Não commite `.env`,
tokens, chaves de API nem caminhos de arquivo de credencial. Se um segredo entrar por
engano, remova do histórico — não basta apagar no commit seguinte.

## 7. Armadilhas já encontradas

- `height="auto"` em SVG é inválido: use `width="100%"` com `preserveAspectRatio`. O mapa já
  apareceu com 170×150 px por causa disso.
- Ao encolher as coordenadas do SVG, **reescale junto o `stroke-width`** — divisas 10× mais
  grossas vieram de um `stroke-width` que ficou em 282 quando as coordenadas foram divididas.
- O centro de um estado não pode ser calculado varrendo o texto do SVG: um código de cor
  (`#d13438`) já entrou na conta e jogou o marcador para o lugar errado.
- Ao injetar lista do Python dentro de string JavaScript, serialize com `json.dumps`. Uma
  lista escrita como texto literal quebrou o script inteiro e a aba parou de ativar.
- O `s` de seções e o `v` de votos do feed contam **só a UF pedida** — não misture com totais
  nacionais.
- Percentual do TSE é calculado sobre **votos válidos**, nunca sobre o total. Brancos e nulos
  não empurram ninguém no 1º turno.
