# Preços de aparelhos novos lacrados

O robô lê a planilha somente para consulta:

- arquivo `cotacao_iphones_lacrados_`;
- aba `BOT`: modelo, capacidade, cores e preço de venda dos aparelhos novos lacrados.

As taxas de parcelamento de 1x a 18x são fixas no robô e não precisam mais ser
lidas da planilha. O estoque continua vindo do cache paginado do Mercado Phone.
A presença de uma linha na aba `BOT` não é usada como prova de estoque.

## Opção recomendada sem chave do Google Cloud

Como a organização bloqueou a criação de chaves de contas de serviço, publique
somente a aba `BOT` como CSV e configure a URL no `.env.local`:

```dotenv
GOOGLE_SHEETS_ENABLED=true
GOOGLE_SHEETS_PUBLIC_CSV_URL=https://docs.google.com/spreadsheets/d/ID/export?format=csv&gid=901822279
```

Troque `ID` pelo ID da planilha. A URL deve retornar o CSV da aba `BOT`, com a
linha de cabeçalho contendo `Modelo`, `Capacidade`, `Cores` e `Preço de Venda
(R$)`. Não coloque a aba de taxas nessa URL: as taxas já estão no código.

Essa alternativa não exige projeto, API habilitada, conta de serviço ou chave.
Como a publicação deixa o conteúdo acessível pela URL, use-a somente se for
aceitável que os preços públicos sejam vistos por quem tiver o endereço.

## Opção privada com conta de serviço

Se um administrador do Google Cloud liberar uma exceção para o projeto:

1. ative a Google Sheets API;
2. crie uma conta de serviço e baixe o JSON;
3. compartilhe a planilha com o `client_email` da conta como Leitor;
4. salve o arquivo na VPS como `secrets/google-service-account.json`;
5. deixe `GOOGLE_SHEETS_PUBLIC_CSV_URL` vazio e `GOOGLE_SHEETS_ENABLED=true`.

Não versione o JSON e não use a credencial da sua conta pessoal na aplicação.

## Atualização e parcelamento

O worker atualiza o cache dos preços a cada hora por padrão. Como você atualiza
os lacrados semanalmente, esse intervalo evita leituras desnecessárias. Se a
fonte estiver indisponível, o robô não inventa preço novo e encaminha a
confirmação quando não houver dado confiável.

As taxas fixas estão em `app/installments.py`:

```text
1x 4,95% | 2x 5,62% | 3x 6,76% | 4x 7,56% | 5x 8,32% | 6x 9,25%
7x 9,94% | 8x 10,82% | 9x 11,73% | 10x 12,40% | 11x 12,96%
12x 14,05% | 13x 15,00% | 14x 15,80% | 15x 16,40% | 16x 17,10%
17x 18,00% | 18x 19,20%
```

O cálculo é: valor da parcela = preço à vista / (1 - taxa) / número de
parcelas. A regra vale para produtos seminovos e lacrados.
