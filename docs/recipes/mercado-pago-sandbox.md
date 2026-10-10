# Mercado Pago: contas e credenciais de teste

Antes de cobrar de verdade, você quer ver um Pix nascer, um cartão ser
aprovado e outro recusado, um reembolso voltar. Tudo isso dá para fazer no
sandbox do Mercado Pago sem mover dinheiro. O difícil é chegar lá: o painel
tem várias credenciais com nomes parecidos, e a maioria das combinações
**não funciona** para cobrar.

Esta página é o caminho que funcionou, passo a passo, com os erros que cada
desvio produz. Ao final você vai ter quatro coisas:

1. uma **conta de teste vendedora** (quem recebe);
2. uma **conta de teste compradora** (quem paga);
3. o **Access Token** da vendedora, de uma aplicação do tipo certo;
4. o **e-mail** da compradora.

!!! info "Medido, não deduzido"
    Cada erro citado aqui foi observado contra o sandbox em 2026-10-09, com a
    credencial e o corpo descritos. O registro está em
    `vendor/mercadopago-evidence.md`, seções 8.5 e 9. O painel do Mercado
    Pago muda de tempos em tempos: se um menu não estiver onde a página diz,
    procure pelo nome da opção.

## Por que não usar a sua própria conta

A primeira tentação é pegar o token `TEST-...` que aparece em "Credenciais de
teste" da **sua** aplicação. Ele lê dados (`GET /v1/payments/search` responde
`200`), mas não cobra:

| Credencial | Pagador | O que o Mercado Pago responde |
| --- | --- | --- |
| `TEST-` da sua conta | sem e-mail | `400 Params Error` (Pix: `500 payer_cannot_be_nil`) |
| `TEST-` da sua conta | e-mail qualquer | `400 excludes_by_rule` (Pix: `500 not_found`) |
| `TEST-` da sua conta | e-mail de comprador de teste | `403 Payer email forbidden` |
| `APP_USR-` da vendedora de teste, aplicação Checkout Pro | qualquer | `401 Unauthorized use of live credentials` |
| `APP_USR-` da vendedora de teste, aplicação **Checkout Transparente / API de Orders** | e-mail da compradora de teste | **`201`, cobrança criada** |

Só a última linha cobra. O resto desta página é como chegar nela.

!!! warning "Nunca use as credenciais de produção da sua conta real"
    O `APP_USR-...` da **sua** conta move dinheiro de verdade. Tudo aqui usa o
    `APP_USR-...` de uma conta **de teste**, que tem esse prefixo também, mas
    não move nada. A forma de conferir está no passo 7.

## Passo 1 — criar as duas contas de teste

Entre com a sua conta normal em
<https://www.mercadopago.com.br/developers/panel/test-users> e crie duas
contas:

- uma do tipo **Vendedor**, país **Brasil**;
- uma do tipo **Comprador**, país **Brasil**.

Para cada uma, anote três coisas que o painel mostra: **usuário**, **senha**
e **User ID**.

!!! tip "O painel não mostra e-mail"
    É normal: a tela de contas de teste mostra usuário e senha, não o e-mail.
    O usuário parece `TESTUSER123456789` e **não** é um e-mail — mandado como
    `payer.email`, o Mercado Pago responde
    `400 payer.email must be a valid email`. O e-mail sai no passo 5.

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

## Passo 5 — pegar o e-mail da compradora

Feche a janela anônima, abra outra e entre com o usuário e a senha da
**compradora**. Clique no seu nome, no canto superior → **Seu perfil** →
**Dados pessoais**. O e-mail aparece lá, no formato
`test_user_...@testuser.com`.

!!! danger "Não invente o e-mail"
    Um e-mail no mesmo formato, mas inventado, não serve: com o token
    `TEST-` ele voltou `403 Payer email forbidden`, e um e-mail qualquer
    (`@example.com`, Gmail) voltou `400 excludes_by_rule`. Use o da conta
    compradora que você criou.

## Passo 6 — guardar fora do repositório

Crie um arquivo **fora** de qualquer repositório, legível só por você:

```bash
mkdir -p ~/.config/meu-servico
cat > ~/.config/meu-servico/mercadopago-sandbox.env <<'EOF'
MERCADO_PAGO_TEST_SELLER_ACCESS_TOKEN=APP_USR-cole-aqui
MERCADO_PAGO_TEST_BUYER_EMAIL=test_user_cole-aqui@testuser.com
EOF
chmod 600 ~/.config/meu-servico/mercadopago-sandbox.env
```

Abra o arquivo no editor e troque os dois valores. Nunca cole o token num
chat, num commit ou num log.

## Passo 7 — conferir que deu certo

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
    buyer: str = os.environ["MERCADO_PAGO_TEST_BUYER_EMAIL"]
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
                "payer": {"email": buyer},
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

Rode carregando o arquivo do passo 6:

```bash
set -a; . ~/.config/meu-servico/mercadopago-sandbox.env; set +a
python conferir_sandbox.py
```

Saída esperada (medida em 2026-10-09):

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
| `401 Unauthorized use of live credentials` | aplicação Checkout Pro, ou token de outra aplicação | passo 3: aplicação Checkout Transparente / API de Orders |
| `403 Payer email forbidden` | token `TEST-` da sua conta com e-mail de teste | passos 2 a 4: use o token da vendedora de teste |
| `400 excludes_by_rule` | e-mail que não é de comprador de teste | passo 5 |
| `400 payer.email must be a valid email` | usuário (`TESTUSER...`) no lugar do e-mail | passo 5 |
| `500 payer_cannot_be_nil` | Pix sem `payer.email` | mande o e-mail da compradora |
| `422 unprocessable_content` | Mastercard de teste | use o Visa de teste |
| *"Não é possível utilizar credenciais de teste em um ambiente de teste"* | você abriu "Credenciais de teste" dentro da conta de teste | passo 4: use as de produção |

## Recapitulando

- Duas contas de teste: **vendedora** recebe, **compradora** paga.
- Entre nelas em janela anônima; o código de verificação é o fim do User ID.
- Aplicação da vendedora: **Checkout Transparente**, **API de Orders**.
- Token: **Credenciais de produção** da conta de teste (`APP_USR-...`).
- E-mail: perfil da compradora, nunca o usuário `TESTUSER...` e nunca um
  inventado.
- Guarde fora do repositório, `chmod 600`, e confira com o script do passo 7
  antes de qualquer outra coisa.
- Cartão de teste que funciona: Visa, titular `APRO` aprova e `OTHE` recusa.

Próximo passo: a receita [Mercado Pago »](mercado-pago.md), agora com as
credenciais em mãos.
