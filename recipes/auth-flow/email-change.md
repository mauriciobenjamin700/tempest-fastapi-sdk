# Trocar e recuperar e-mail

Use para trocar o e-mail logado, re-verificar o atual, recuperar a conta quando a caixa se perdeu e renovar a sessão com o refresh token.

Parte da receita [Bundled auth flow (signup / activate / login / reset)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/auth-flow.md).

O espelho do fluxo de senha, mas pro **e-mail** da conta (v0.92.0+). Três
cenários distintos — comece pelo seu.

## Trocar e-mail (logado)

O caso base: o usuário **está logado** e quer mudar o próprio e-mail.
Confirma a `current_password` e informa o `new_email`; um link de
confirmação vai pro **novo** endereço. O e-mail só muda quando esse link
é confirmado.

```bash
# Passo 1 — pedir a troca (autenticado). Sempre 202.
curl -X POST localhost:8000/auth/email-change/request \
  -H "Authorization: Bearer eyJhbGciOi…" \
  -H "Content-Type: application/json" \
  -d '{"new_email": "nova@example.com", "current_password": "senha-atual"}'

# Passo 2 — confirmar (o front lê o token do link enviado ao novo e-mail).
curl -X POST localhost:8000/auth/email-change/confirm \
  -H "Content-Type: application/json" \
  -d '{"token": "abc123…"}'
```

Ao confirmar, se `AUTH_EMAIL_CHANGE_NOTIFY_OLD=True` (padrão) um **aviso
de segurança vai pro endereço antigo** — o padrão de bancos/Google, pra
uma conta sequestrada ainda alertar o dono. A confirmação também **revoga
as sessões** do usuário (refresh tokens e cookies das páginas), como a
troca de senha — `confirm_email_change(..., revoke_sessions=False)` é a
saída explícita no service. `email_verified_at` passa a valer para o endereço
novo: carimbado quando o link só chegou por e-mail, limpo quando voltou na
resposta.

!!! check "Erros"
    - Senha atual errada → **401**.
    - `new_email` já em uso (no pedido **ou** na confirmação — corrida) → **409**.
    - Token inválido / expirado / usado → **401** com `code: "INVALID_TOKEN"`.

## Re-verificar o e-mail atual

Reenvia um link de verificação pro **endereço atual** — útil quando o
e-mail de ativação se perdeu. Autenticado, sem trocar nada; confirmar o
link marca a conta ativa.

```bash
curl -X POST localhost:8000/auth/email-verify/request \
  -H "Authorization: Bearer eyJhbGciOi…"
# depois: POST /auth/email-verify/confirm  {"token": "…"}
```

É também o caminho para uma conta ativada pelo admin, ou criada antes da
coluna `email_verified_at`, provar o endereço.

## O que conta como e-mail verificado

O `BaseUserModel` guarda **quando** o dono da conta provou que lê o endereço
atual em `email_verified_at` (`null` quando nunca provou), e o `GET /auth/me`
devolve a coluna. `is_active` não serve para isso: uma conta fica ativa pelo
admin, por `tempest user activate`, com `AUTH_AUTO_ACTIVATE=true` ou por um link
que voltou na resposta — e nenhum desses caminhos passou pela caixa de entrada.

O SDK carimba a coluna só ao confirmar um link de ativação ou de verificação
que foi **enviado por e-mail para aquele endereço** (com
`AUTH_RETURN_TOKEN_IN_RESPONSE=false`, `AUTH_AUTO_ACTIVATE=false` e
`EmailUtils` ligado), ao confirmar uma troca de e-mail nas mesmas condições, e
ao criar conta pelo [login social](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/oauth.md) com `email_verified is True`.
Mudar o `email` por qualquer outro caminho do ORM (admin, script) limpa a
coluna; um `UPDATE` em massa não passa pelo ORM e precisa limpá-la ele mesmo.
Quem lê a coluna hoje é a ligação por e-mail do login social
(`AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL`).

## Recuperação de e-mail

O "esqueci / perdi acesso ao meu e-mail". Endpoint **não autenticado** e
**opt-in** — só é montado com `AUTH_EMAIL_RECOVERY_ENABLED=True`. O
usuário prova identidade com a **senha** (e um **código MFA** quando o
TOTP está inscrito) e informa o novo endereço; o link de confirmação vai
pro **novo** e-mail e reusa o mesmo `email-change/confirm`.

```bash
# Só existe se AUTH_EMAIL_RECOVERY_ENABLED=True. Sempre 202 (anti-enumeração).
curl -X POST localhost:8000/auth/email-recovery/request \
  -H "Content-Type: application/json" \
  -d '{
        "email": "antigo@example.com",
        "new_email": "novo@example.com",
        "current_password": "senha-atual",
        "mfa_code": "123456"
      }'
# depois: POST /auth/email-change/confirm  {"token": "…"}  (link foi pro novo e-mail)
```

!!! danger "Recuperação é sensível — ligue com cuidado"
    Esse endpoint deixa quem tem a **senha** mover a conta pra outro
    e-mail **sem** acessar a caixa antiga — vetor de account takeover se a
    senha vazou. Regras:

    - **Opt-in**: fica desligado até `AUTH_EMAIL_RECOVERY_ENABLED=True`.
    - Sempre **202** genérico pra toda falha leve (e-mail desconhecido,
      senha errada, `mfa_code` faltando/errado) — não enumera contas.
    - Se o TOTP estiver inscrito, exige um `mfa_code` válido.
    - Gasta os **mesmos orçamentos** do `/auth/login` (por e-mail e, se
      configurado, por IP) e do `/auth/mfa/verify` (por conta): depois de
      esgotados, nem a senha nem o código certos emitem link — a resposta
      continua o `202` genérico.
    - Mantenha `AUTH_EMAIL_CHANGE_NOTIFY_OLD=True` pra o e-mail antigo
      sempre ser avisado da troca.

!!! info "Onde o novo e-mail fica guardado"
    O endereço pendente viaja na coluna `payload` do token
    (`EMAIL_CHANGE`), não no `UserModel`. A confirmação relê o `payload`,
    re-checa que o endereço ainda está livre (corrida entre pedido e
    confirmação → **409**) e só então grava. Veja a nota de migração da
    0.92.0.

## Renovando a sessão com o refresh token

O `access_token` é curto por design (`JWT_ACCESS_TTL_SECONDS`, 1 h por
padrão). Quando ele expira, **não force o usuário a logar de novo** —
troque o `refresh_token` (longo, 7 dias por padrão) por um par novo em
`POST /auth/refresh`:

```bash
curl -X POST localhost:8000/auth/refresh \
  -H "Content-Type: application/json" \
  -d '{"refresh_token": "eyJhbGciOi…"}'
```

```json
{
  "user_id": "7d8e4d5a-9f4b-4c3a-bd0a-1234567890ab",
  "access_token": "eyJhbGciOi…(novo)",
  "refresh_token": "eyJhbGciOi…(novo)",
  "mfa_required": false,
  "mfa_token": null
}
```

O endpoint decodifica o token, exige que ele realmente carregue a claim
`refresh` (um **access** token replayado aqui é rejeitado com **401**),
resolve o `sub` pra um usuário **ativo** e emite um par novo.

!!! warning "Os dois tokens rotacionam"
    A resposta traz um `refresh_token` **novo**. Persista esse e descarte
    o que você enviou. No modo **stateless** (default) o SDK emite JWTs,
    então o par antigo não é revogado — ele continua válido até o próprio
    `exp`.

!!! tip "Precisa de revogação real? O SDK já entrega"
    Não escreva sua própria tabela de refresh tokens — desde a
    **v0.66.0** o SDK oferece refresh tokens **DB-backed opacos** com
    rotação single-use, **detecção de reuso** (token roubado revoga a
    família inteira) e `POST /auth/logout`. É opt-in: passe um
    `refresh_token_model` pro `UserAuthService`. Veja a receita
    [Refresh tokens (rotação/revogação)](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/refresh-tokens.md).

!!! tip "Quando o refresh token também expira"
    Aí não tem renovação possível — o **401** é definitivo e o cliente
    cai de volta no `POST /auth/login` com email + senha.

Sem frontend? O método de serviço por trás do endpoint é público —
`await service.refresh_tokens(session, refresh_token=...)` devolve
`(user, access_token, refresh_token)`.
