# Reenviar ativação e recuperar senha

Use quando o e-mail de ativação não chegou, quando o usuário esqueceu a senha ou quer trocá-la logado.

Parte da receita [Bundled auth flow (signup / activate / login / reset)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow.md).

## Reenviar a ativação

O e-mail de ativação sai **uma vez**, no `signup`. Quando ele não chega —
filtro de spam, relay que devolveu, aba fechada antes de clicar — a conta
fica sem saída:

- `signup` de novo responde **409**, o e-mail já está na tabela;
- `login` responde **401**, porque `is_active` ainda é `False`;
- `email-verify/request` existe para exatamente esse caso, mas exige
  **bearer token** — que só sai de um login bem-sucedido.

Isso é um beco sem saída que só o acesso ao banco resolvia.
`POST /auth/activation/request` é a saída, e ela **não pede credencial
nenhuma** por necessidade:

```bash
curl -X POST localhost:8000/auth/activation/request \
  -H "Content-Type: application/json" \
  -d '{"email": "ana@example.com"}'
```

```json
{
  "message": "If the email matches a pending account, a link was sent.",
  "activation_url": "http://localhost:3000/activate?token=..."
}
```

O `activation_url` só aparece quando você mesmo entrega o link —
`AUTH_RETURN_TOKEN_IN_RESPONSE=True` ou `EmailUtils` não configurado. Em
produção ele vem `null` e o link viaja por e-mail, no idioma que o
[i18n](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow/i18n.md#idioma-dos-e-mails-e-paginas-i18n) resolver.

!!! info "202 sempre, com a mesma frase"
    Endereço desconhecido, conta **já ativa** e reenvio de verdade
    respondem o mesmo status e o mesmo corpo. É o mesmo cuidado
    anti-enumeração do `password-reset/request`: quem sondar a rota não
    descobre quais e-mails têm conta aqui.

!!! warning "Conta já ativa não recebe e-mail"
    Reemitir para quem já ativou mandaria um link que não autoriza nada — e
    contaria a um estranho que aquele endereço tem conta. Por isso o caminho
    para de silenciosamente.

O token novo segue as mesmas regras do original: TTL de
`AUTH_ACTIVATION_TTL_SECONDS` e, com `AUTH_SINGLE_ACTIVE_TOKEN=True` (o
default), o pedido anterior é queimado — só o link mais recente funciona.

---

## Recuperação de senha

O clássico "esqueci minha senha". O usuário **não está logado** e prova
identidade com um **token de uso único** que chega por e-mail. São dois
passos.

### Caso base (SPA / JSON)

**Passo 1 — pedir o link.** O usuário digita o e-mail; o backend sempre
responde **202** com a mesma mensagem genérica (não vaza se o e-mail
existe). A mensagem é fixa no código, em inglês:

```bash
curl -X POST localhost:8000/auth/password-reset/request \
  -H "Content-Type: application/json" \
  -d '{"email": "ana@example.com"}'
```

```json
{"message": "If the email matches an account, a reset link was sent.", "reset_url": null}
```

**Passo 2 — trocar a senha.** O usuário abre o link do e-mail; o
front-end lê o `token` da URL e o envia com a nova senha. Em caso de
sucesso a senha é trocada **e o usuário já sai logado** (vem um JWT pair):

```bash
curl -X POST localhost:8000/auth/password-reset/confirm \
  -H "Content-Type: application/json" \
  -d '{"token": "abc123…", "new_password": "nova-senha-forte-12"}'
```

```json
{
  "user_id": "7d8e4d5a-…",
  "access_token": "eyJhbGciOi…",
  "refresh_token": "eyJhbGciOi…",
  "mfa_required": false,
  "mfa_token": null
}
```

!!! warning "Conta com MFA: o reset termina no `/auth/mfa/verify`"
    Se a conta tem TOTP inscrito (e `AUTH_MFA_ENABLED=True`), o
    `confirm` **não** devolve o par — devolve `mfa_required=true` +
    `mfa_token`, igual ao `/login`. É o que impede um e-mail
    comprometido de pular o segundo fator: a senha é trocada, mas a
    sessão só nasce depois do código. Conclua o login mandando o
    `mfa_token` e o código TOTP:

    ```bash
    curl -X POST localhost:8000/auth/mfa/verify \
      -H "Content-Type: application/json" \
      -d '{"mfa_token": "eyJ…", "code": "123456"}'
    ```

    O par que sai daí carrega `amr == ["email", "otp"]`; sem MFA, o par do
    próprio `confirm` carrega `["email"]` *(v0.314.0+)*. Veja a
    [receita de MFA](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/mfa.md).

!!! note "O reset derruba as sessões já abertas"
    Rotacionar credencial encerra o que foi aberto com a antiga: trocar a
    senha por este caminho **revoga** os refresh tokens que já existiam,
    na mesma transação que grava o hash — uma sessão aberta antes do reset
    deixa de trocar o refresh token. (Access tokens já emitidos valem até
    o TTL curtinho.) É o mesmo default do `/auth/mfa/confirm` e do
    `tempest user set-password`; quem chama o service com
    `revoke_sessions=False` abre mão disso de propósito.

!!! note "O reset também apaga as passkeys"
    Com [WebAuthn](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/webauthn.md) montado, o reset apaga **todas** as passkeys
    do usuário na mesma transação: uma passkey entra sem a senha, e a que
    alguém cadastrou enquanto segurava a conta continuaria entrando depois
    da recuperação. As do próprio dono vão junto, e ele cadastra de novo.
    `confirm_password_reset(..., revoke_passkeys=False)` é a saída
    explícita. Veja
    [Reset de senha e de MFA apagam as passkeys](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/webauthn.md#reset-de-senha-e-de-mfa-apagam-as-passkeys).

!!! check "Erros que você vai ver"
    - Token desconhecido / já usado / expirado → **401** com `code: "INVALID_TOKEN"` e um `detail` que diz o motivo (token desconhecido: `token not recognized`).
    - `token` com menos de 16 caracteres → **422** do próprio FastAPI (`string_too_short`), antes de chegar ao serviço.
    - `new_password` viola `AUTH_PASSWORD_MIN_LENGTH` / complexidade → **422**.
    - `request` **nunca** erra por e-mail inexistente — sempre **202**.

!!! note "Modo dev: link no body (sem SMTP)"
    Sem o extra `[email]`, ou com `AUTH_RETURN_TOKEN_IN_RESPONSE=True`, o
    `reset_url` volta **no corpo** do `request` em vez de ir por e-mail —
    você dispara o fluxo inteiro sem inbox. Em produção ele fica `null`:
    com `ENV=production` (`EnvironmentSettings` composto), a flag derruba o
    boot, e um `UserAuthService` sem `email=` responde **503**
    `INTEGRATION_DISABLED` a todo fluxo que levaria link — o link nunca
    volta para quem só digitou o e-mail.

!!! tip "Conta que não usa link de e-mail *(v0.316.0+)*"
    O `UserAuthService(refuses_email_link=...)` fecha este fluxo (e o de
    ativação) para contas escolhidas por um predicado — um operador, um
    staff. O `request` responde o mesmo `202` e não manda e-mail; a
    confirmação devolve **403** `EMAIL_LINK_FORBIDDEN` sem trocar a senha; e
    o `signup` cria a conta inativa sem gravar nem mandar link de ativação,
    com a mesma resposta de um cadastro pendente comum.
    Veja a [receita de MFA](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/mfa.md#email-link-refusal).

??? note "Sem front-end? Páginas HTML pelo backend (Modo E)"
    Com `AUTH_BACKEND_LINKS=True` o SDK monta **GET/POST**
    `/auth/password-reset/{token}`: o link do e-mail aponta pro backend,
    que renderiza um formulário HTML, valida e mostra uma página de
    sucesso/erro — sem nenhuma rota no front-end. Veja
    [Cinco modos de operação](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow/modes.md#cinco-modos-de-operacao) (Modo E).

### Trocar a senha (logado)

Diferente do reset: aqui o usuário **lembra** a senha e **está logado**.
Não há token nem e-mail — ele manda o `access_token` no header e
reconfirma a `current_password`:

```bash
curl -X POST localhost:8000/auth/password-change \
  -H "Authorization: Bearer eyJhbGciOi…" \
  -H "Content-Type: application/json" \
  -d '{"current_password": "senha-atual", "new_password": "nova-senha-forte-12"}'
```

**Revoga as sessões** do usuário na mesma transação que grava a senha
nova, como o reset: refresh token emitido antes da troca — inclusive o de
quem chamou — deixa de ser trocável, e o cookie de sessão das
[páginas de auth](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-pages.md) também cai. Para o dispositivo que trocou
a senha continuar logado, a resposta é **200** com um **par novo**
(até a 0.335.0 era `204` sem corpo — veja o
[guia de migração](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/migration.md)), emitido depois da revogação — é o único refresh token que
sobra:

```json
{
  "user_id": "7d8e4d5a-9f4b-4c3a-bd0a-1234567890ab",
  "access_token": "eyJhbGciOi…",
  "refresh_token": "Zm9vYmFy…",
  "mfa_required": false,
  "mfa_token": null
}
```

Guarde os dois e descarte os antigos. Quem se autenticou pelo cookie de
acesso recebe o par em cookies, e o corpo traz só o `user_id`.
`current_password` errada → **401**, sem par.

A regra é: a rota **nunca** devolve uma sessão mais forte do que o
`/auth/login` devolveria para as mesmas provas. A senha atual prova só o
primeiro fator, então numa conta **com MFA**:

| Corpo | O que acontece |
| --- | --- |
| sem `code` | A senha muda e as sessões caem; a resposta é o desafio do login (`mfa_required: true`, `mfa_token`), sem par. Complete em `POST /auth/mfa/verify` como depois do login |
| `code` válido (TOTP ou recovery code) | O código é gasto antes da troca (o mesmo passo TOTP não vale duas vezes); a senha muda e vem um par com `amr == ["pwd", "otp"]` |
| `code` errado | **401**: nada muda, nem a senha nem as sessões |

```bash
curl -X POST localhost:8000/auth/password-change \
  -H "Authorization: Bearer eyJhbGciOi…" \
  -H "Content-Type: application/json" \
  -d '{"current_password": "senha-atual", "new_password": "nova-senha-forte-12", "code": "123456"}'
```

O `amr` do access token atual **não** é copiado para o par novo: numa conta
sem MFA o par sai com `["pwd"]` (ou os métodos do token que não são segundo
fator), e o segundo fator só aparece quando um `code` foi provado nesta
requisição. Chamando o service direto,
`change_password(..., revoke_sessions=False)` é a saída explícita. As
passkeys ficam: quem chama acabou de provar a senha atual, então não é
recuperação de conta.

!!! tip "Reset vs change — qual é qual?"
    - **Esqueceu** a senha, não está logado → **[Recuperação de senha](#recuperacao-de-senha)** (`password-reset/request` → `confirm`, prova com token de e-mail).
    - **Lembra** a senha, está logado → **trocar a senha** (`password-change`, prova com a senha atual no header).
