# Mercado Pago: contas e credenciais de teste

Antes de cobrar de verdade, você quer ver um Pix nascer, um cartão ser
aprovado e outro recusado, um reembolso voltar. Tudo isso dá para fazer no
sandbox do Mercado Pago sem mover dinheiro. O difícil é chegar lá: o painel
tem várias credenciais com nomes parecidos, e a maioria das combinações
**não funciona** para cobrar.

Esta página é o caminho que funcionou, passo a passo, com os erros que cada
desvio produz. No fim você vai ter duas coisas:

1. uma **conta de teste vendedora** (quem recebe);
2. o **Access Token** dela, de uma aplicação do tipo certo.

E só. O pagador pode ser qualquer e-mail válido — medido, no passo 5.

!!! info "Medido, não deduzido"
    Cada resposta citada aqui foi observada contra o sandbox entre 2026-10-09
    e 2026-10-10, na API de Orders, com a credencial e o corpo descritos. O
    registro está em `vendor/mercadopago-evidence.md`, seção 9. O painel do
    Mercado Pago muda de tempos em tempos: se um menu não estiver onde a
    página diz, procure pelo nome da opção.

## Por que não usar a sua própria conta

A primeira tentação é pegar o token `TEST-...` que aparece em "Credenciais de
teste" da **sua** aplicação. Ele lê dados, mas não cobra:

| Credencial | Pagador | O que a API de Orders responde |
| --- | --- | --- |
| `TEST-` da sua conta | qualquer um, ou nenhum | `403 At least one policy returned UNAUTHORIZED.` |
| `APP_USR-` da vendedora de teste, aplicação Checkout Pro | qualquer | `401 Unauthorized use of live credentials` (medido na API de Payments) |
| `APP_USR-` da vendedora de teste, aplicação **Checkout Transparente / API de Orders** | qualquer e-mail válido | **`201`, cobrança criada** |

Só a última linha cobra. O resto desta página é como chegar nela.

!!! warning "Nunca use as credenciais de produção da sua conta real"
    O `APP_USR-...` da **sua** conta move dinheiro de verdade. Tudo aqui usa o
    `APP_USR-...` de uma conta **de teste**, que tem esse prefixo também, mas
    não move nada. A forma de conferir está no passo 6.

## Passo 1 — criar a conta de teste vendedora

Entre com a sua conta normal em
<https://www.mercadopago.com.br/developers/panel/test-users> e crie uma
conta do tipo **Vendedor**, país **Brasil**. Anote o que o painel mostra:
**usuário**, **senha** e **User ID**.

!!! tip "E a conta compradora?"
    Para cobrar pela API ela não é necessária: o pagador da order pode ser
    qualquer e-mail válido (passo 5). Crie uma só se for testar uma tela em
    que alguém faz login no Mercado Pago para pagar.

## Passo 2 — entrar como a vendedora

Abra uma **janela anônima** (assim você não mistura com a sua conta real) e
entre em <https://www.mercadopago.com.br/> com o usuário e a senha da
**vendedora**.

Se o Mercado Pago pedir um código de verificação, use os **últimos 6 dígitos
do User ID** da conta de teste.

## Passo 3 — criar a aplicação do tipo certo

Ainda logado como vendedora, abra
<https://www.mercadopago.com.br/developers/panel/app> e clique em **Criar
aplicação**:

1. **Solução**: escolha **Checkout Transparente**. Não escolha Checkout Pro:
   com ela o token cria preferência de checkout, mas recusa cobrança direta
   com `401 Unauthorized use of live credentials`.
2. **Tipo de API**: escolha **API de Orders**. A API de Payments aparece com
   o aviso "Esta API será descontinuada em breve", e este SDK fala com a de
   Orders.
3. **Nome**: qualquer um.

## Passo 4 — copiar o Access Token da vendedora

Na aplicação recém-criada, abra **Credenciais de produção** e copie o
**Access Token** (`APP_USR-...`).

É o token **de produção da conta de teste**, e é ele mesmo que você quer.
Dois caminhos que parecem certos e não são:

- **"Credenciais de teste"** dentro da conta de teste: o painel responde
  *"Não é possível utilizar credenciais de teste em um ambiente de teste"*.
- **"Ativar credenciais"**: em conta de teste a opção não aparece, e não é
  necessária.

Guarde o token **fora** de qualquer repositório, legível só por você:

```bash
mkdir -p ~/.config/meu-servico
printf 'MERCADO_PAGO_TEST_SELLER_ACCESS_TOKEN=APP_USR-cole-aqui\n' \
  > ~/.config/meu-servico/mercadopago-sandbox.env
chmod 600 ~/.config/meu-servico/mercadopago-sandbox.env
```

Abra o arquivo no editor e troque o valor. Nunca cole o token num chat, num
commit ou num log.

## Passo 5 — o e-mail do pagador

Com o token certo, a order aceita **qualquer e-mail válido** como pagador:
`comprador@example.com`, um Gmail, um `test_user_…@testuser.com` inventado —
Pix e cartão, todos `201`. O que ela recusa é a falta dele:

| `payer` enviado | Resposta |
| --- | --- |
| ausente, ou `{}` | `400 '$.payer' - minimum 1 properties allowed, but found 0 properties` |
| só `first_name` | `400 '$.payer.email' or '$.payer.customer_id' or '$.payer.id'` |
| o usuário `TESTUSER…` no lugar do e-mail | `400 '$.payer.email' - does not match pattern` |
| qualquer e-mail válido | `201` |

!!! note "Se você viu outros erros de pagador"
    `500 payer_cannot_be_nil`, `400 excludes_by_rule` e
    `403 Payer email forbidden` foram medidos na **API de Payments**
    (`/v1/payments`), que exige um comprador de teste de verdade. Se eles
    aparecem, o código está chamando a API descontinuada.

## Passo 6 — conferir que deu certo

O script abaixo faz duas coisas. Primeiro, confere que o token é de uma
conta **de teste** — se não for, ele para sem cobrar nada. Depois, cria um
Pix de R$ 19,90 pela API de Orders e o cancela em seguida.

```python
import asyncio
import os
import uuid
from typing import Any

from tempest_fastapi_sdk import HTTPClient

BASE_URL: str = "https://api.mercadopago.com"


async def main() -> None:
    """Check the token is a test account, then open and cancel a Pix order."""
    token: str = os.environ["MERCADO_PAGO_TEST_SELLER_ACCESS_TOKEN"]
    async with HTTPClient(
        base_url=BASE_URL,
        default_headers={"Authorization": f"Bearer {token}"},
    ) as http:
        me: dict[str, Any] = (await http.request("GET", "/users/me")).json()
        if "test_user" not in (me.get("tags") or []):
            raise SystemExit("Este token não é de conta de teste. Parando.")

        created = await http.request(
            "POST",
            "/v1/orders",
            headers={"X-Idempotency-Key": str(uuid.uuid4())},
            json={
                "type": "online",
                "processing_mode": "automatic",
                "total_amount": "19.90",
                "external_reference": "teste-sandbox-1",
                "payer": {"email": "comprador@example.com"},
                "transactions": {
                    "payments": [
                        {
                            "amount": "19.90",
                            "expiration_time": "PT30M",
                            "payment_method": {"id": "pix", "type": "bank_transfer"},
                        }
                    ]
                },
            },
        )
        order: dict[str, Any] = created.json()
        print(created.status_code, order.get("status"), order.get("status_detail"))

        cancelled = await http.request(
            "POST",
            f"/v1/orders/{order['id']}/cancel",
            headers={"X-Idempotency-Key": str(uuid.uuid4())},
        )
        print(cancelled.status_code, cancelled.json().get("status"))


asyncio.run(main())
```

Rode carregando o arquivo do passo 4:

```bash
set -a; . ~/.config/meu-servico/mercadopago-sandbox.env; set +a
python conferir_sandbox.py
```

Saída esperada (medida em 2026-10-10):

```text
201 action_required waiting_transfer
200 canceled
```

`action_required` / `waiting_transfer` é um Pix esperando pagamento. Se você
viu isso, está tudo pronto. 🎉

## Cartões de teste

Para testar cartão, tokenize um cartão de teste e mande o token na order.
O titular escrito no cartão decide o resultado:

| Cartão | Validade / CVV | Titular | Resultado medido |
| --- | --- | --- | --- |
| Visa `4235 6477 2802 5682` | `11/2030` / `123` | `APRO` | `201`, `processed` / `accredited` |
| Visa `4235 6477 2802 5682` | `11/2030` / `123` | `OTHE` | `402`, `rejected_by_issuer` |
| Mastercard `5031 4332 1540 6351` | `11/2030` / `123` | `APRO` | `422 unprocessable_content` |

!!! note "Recusa é HTTP 402, não erro de servidor"
    Um cartão recusado volta **402** com o motivo em `errors` e a order
    inteira em `data`. Trate como resposta, não como falha de rede: o pedido
    existe, foi recusado, e o motivo está ali.

!!! warning "Use o Visa"
    O Mastercard de teste que circula na documentação respondeu `422`
    genérico na API de Orders, com o mesmo token que aprovou o Visa. O
    motivo não foi isolado.

## Erros comuns

| Mensagem | Causa provável | O que fazer |
| --- | --- | --- |
| `403 At least one policy returned UNAUTHORIZED.` | token `TEST-` da sua conta | passos 1 a 4: use o token da vendedora de teste |
| `401 Unauthorized use of live credentials` | aplicação Checkout Pro, ou token de outra aplicação | passo 3: aplicação Checkout Transparente / API de Orders |
| `400 '$.payer' - minimum 1 properties allowed` | order sem pagador | passo 5: mande um e-mail |
| `400 '$.payer.email' - does not match pattern` | usuário (`TESTUSER...`) no lugar do e-mail | passo 5 |
| `422 unprocessable_content` | Mastercard de teste | use o Visa de teste |
| *"Não é possível utilizar credenciais de teste em um ambiente de teste"* | você abriu "Credenciais de teste" dentro da conta de teste | passo 4: use as de produção |

## Recapitulando

- Uma conta de teste **vendedora**; a compradora é opcional para a API.
- Entre nela em janela anônima; o código de verificação é o fim do User ID.
- Aplicação da vendedora: **Checkout Transparente**, **API de Orders**.
- Token: **Credenciais de produção** da conta de teste (`APP_USR-...`).
- Pagador: qualquer e-mail válido; sem ele, `400`.
- Guarde fora do repositório, `chmod 600`, e confira com o script do passo 6
  antes de qualquer outra coisa.
- Cartão de teste que funciona: Visa, titular `APRO` aprova e `OTHE` recusa.

Próximo passo: a receita [Mercado Pago »](mercado-pago.md), agora com a
credencial em mãos.
