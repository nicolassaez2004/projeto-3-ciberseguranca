# Return Tech — avaliação automática de código

O candidato escreve o programa no editor e envia a solução para correção. O backend usa os casos privados cadastrados pela empresa, consulta o Judge0 e registra a melhor pontuação no MongoDB.

## Aplicar esta atualização

Os ZIPs contêm os projetos completos e preservam exatamente os arquivos `.env` recebidos. Eles incluem credenciais: mantenha-os privados. Não publique esses arquivos no GitHub.

1. Pare o backend e o processo da fila com `Ctrl+C` nos respectivos terminais.
2. Extraia os ZIPs em uma pasta temporária e copie o conteúdo de cada projeto para sua pasta existente, substituindo os arquivos.
3. Preserve as pastas `.git`, `.venv` e `node_modules` e a branch em que você está trabalhando.
4. Reinicie o backend e o processo da fila pelos comandos abaixo. Reinicie o frontend e atualize a página.

Esta revisão não altera dependências. As notas proporcionais valem para novos envios e para correções ainda em andamento. Notas já concluídas e desafios existentes são preservados; reenvie o código para obter uma nota pela nova regra.

## Configuração

Existe somente um `.env` em cada projeto. O backend carrega explicitamente o arquivo da pasta de `run.py` e preserva a prioridade de variáveis definidas pelo ambiente de hospedagem.

No backend, mantenha MongoDB, JWT e as variáveis existentes. Para o Judge0 local já configurado no Windows:

```env
JUDGE0_URL=http://127.0.0.1:2358
JUDGE0_API_KEY=
JUDGE0_RAPIDAPI_HOST=
JUDGE0_AUTH_TOKEN=seu-token-privado
```

O token deve corresponder ao `AUTHN_TOKEN` da instância local. Em serviços RapidAPI, use a URL, a chave e o host fornecidos pelo provedor. Use HTTPS para um executor remoto; HTTP é aceito somente em localhost.

No frontend, a configuração pública é:

```env
VITE_API_URL=http://localhost:5000/api
```

Credenciais de MongoDB, JWT e Judge0 ficam somente no backend. Após alterar o `.env`, reinicie os processos correspondentes.

## Iniciar no Windows

Mantenha a VM e os containers do Judge0 em funcionamento. O proxy local do Windows precisa apontar para o IP atual da VM.

Na pasta `Return_Tech_Backend`:

```powershell
.\.venv\Scripts\python.exe run.py
```

Em outro terminal da mesma pasta:

```powershell
.\.venv\Scripts\python.exe -m flask --app run process-code-jobs --watch
```

Na pasta `Return_Tech_Frontend`:

```powershell
npm run dev
```

O processo da fila conclui correções mesmo quando o candidato fecha o navegador. Em hospedagem, mantenha esse processo supervisionado separadamente do servidor web.

## Fluxo da empresa

1. Abra **Publicar desafio**, preencha os campos e salve como rascunho.
2. No cartão, abra **Configurar código**.
3. Cadastre o exemplo público, as linguagens e os casos privados em JSON.
4. Salve a avaliação e publique o desafio.
5. Consulte **Código e pontos** para acompanhar as soluções e a melhor nota de cada candidato.

A configuração é bloqueada após a publicação ou quando já há tentativas. Não existe botão ou rota para gerar desafios de demonstração. Desafios já cadastrados no banco não são apagados por esta atualização.

O JSON aceita:

| Campo | Uso |
|---|---|
| `languages` | Lista contendo `python`, `javascript` ou `cpp` |
| `max_points` | Inteiro entre 1 e 10.000 |
| `cpu_time_limit` | CPU por caso, entre 0,5 e 5 segundos |
| `memory_limit_kb` | Memória entre 64.000 e 256.000 KB |
| `example` | Objeto com `stdin` e `expected_output`, visível no enunciado |
| `starter_code` | Código inicial opcional por linguagem |
| `tests` | De 1 a 20 casos privados com `stdin` e `expected_output` |

Cada entrada ou saída esperada tem limite de 16 KB. Todos os casos privados têm o mesmo peso. O programa deve ler a entrada padrão e imprimir o resultado na saída padrão. Os casos precisam refletir as regras do enunciado; o Judge0 não deduz essas regras sozinho.

## Fluxo do candidato

O candidato participa do desafio, abre o editor, escreve o programa e clica em **Enviar solução**. O exemplo de entrada e saída é somente leitura. Não existe campo de entrada experimental ou botão Executar.

A correção mostra a pontuação e o status de cada caso. Entradas privadas, saídas privadas e tokens do executor não são retornados ao candidato. O candidato pode fechar o editor durante a correção e voltar depois. O rascunho fica no `sessionStorage`, separado por usuário, desafio e linguagem.

O limite do código é de 64 KB. O backend verifica inscrição, prazo e linguagem, permite uma tentativa ativa por candidato e desafio e aplica três segundos de intervalo entre envios. O prazo informado como data termina no final daquele dia no fuso de São Paulo.

## Pontuação proporcional

A pontuação é calculada como `pontos máximos × casos corretos ÷ total de casos`, arredondada para duas casas decimais, com metades arredondadas para cima.

| Casos corretos | Total | Máximo | Nota |
|---|---|---|---|
| 3 | 6 | 100 | 50 |
| 4 | 6 | 100 | 66,67 |
| 6 | 6 | 100 | 100 |
| 0 | 6 | 100 | 0 |

A interface formata as notas em português. A melhor nota é mantida por candidato e desafio com uma atualização atômica `$max`. Reenvios e consultas repetidas não somam notas; uma tentativa pior não reduz a melhor nota. O desafio só recebe a marca de resolução completa quando todos os casos passam.

Resposta incorreta, erro do programa e limite de tempo não dão pontos naquele caso. Falhas de comunicação mantêm a tentativa pendente para nova consulta. Erros internos do executor deixam a tentativa sem nota, preservando a melhor pontuação. Após cinco minutos sem conclusão, a tentativa é encerrada como falha do serviço e permite novo envio.

As notas automáticas continuam separadas das avaliações humanas e da shortlist.

## API

| Método e rota | Acesso |
|---|---|
| `GET /api/challenges/<id>/coding` | Candidato inscrito ou empresa proprietária |
| `GET/PUT /api/challenges/<id>/coding/config` | Empresa proprietária; alteração apenas em rascunho sem tentativas |
| `POST /api/challenges/<id>/code-jobs` | Candidato inscrito, desafio publicado e dentro do prazo |
| `GET /api/code-jobs/<id>` | Dono da tentativa ou empresa proprietária |
| `GET /api/challenges/<id>/coding/results` | Empresa proprietária |

Envio:

```json
{
  "mode": "submit",
  "language": "python",
  "source_code": "print(2)"
}
```

`mode` é opcional e assume `submit`. O backend recusa `mode: "run"` e entradas `stdin` fornecidas pelo candidato. A criação responde com HTTP 202 e `job._id`. Consulte a tentativa até `completed` ou `service_error`.

Tentativas experimentais antigas podem ser finalizadas pelo processo da fila para compatibilidade, mas não aparecem como última solução no editor e não recebem pontos. A correção da decodificação Base64 do Judge0 foi preservada.

As coleções usadas são `coding_configs`, `coding_jobs` e `coding_scores`. O backend mantém os índices necessários na inicialização. Nenhum programa de candidato é executado pelo Flask: a execução ocorre no Judge0 com rede desabilitada e limites de tempo, memória, processos e arquivos.

## Verificação de desenvolvimento

Os testes automatizados pertencem ao desenvolvimento e não aparecem no fluxo do usuário. Usam MongoDB e Judge0 simulados, sem acessar o banco da equipe.

No backend:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

No frontend:

```powershell
npm ci
npm test
npm run build
```

A execução real depende da instância do Judge0 e da configuração da máquina em que o projeto é iniciado.
