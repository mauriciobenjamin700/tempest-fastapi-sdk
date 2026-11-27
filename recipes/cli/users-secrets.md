# Usuários e segredos

Use para criar e administrar usuários (inclusive MFA) e gerar, preencher e girar os segredos do `.env` (`tempest user`, `tempest secrets`).

Parte da receita [CLI](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/cli.md).

## Usuários — `tempest user`

Insere/lista usuários direto no banco usando o `UserModel` concreto do projeto (default `src.db.models:UserModel`). Útil pra bootstrapear o primeiro admin sem rodar SQL manual.

```bash
# Cria usuário comum
tempest user create --email ana@example.com --password senha-forte-12 --no-admin

# Cria admin (pode logar no /admin)
tempest user create --email admin@local --password admin-pass-12 --admin

# Pede senha interativamente (não fica no shell history)
tempest user create --email admin@local --admin

# Modelo customizado fora do layout scaffoldado — DEVE ser subclasse de BaseUserModel
tempest user create --email x@y --password pass-12-chars --model myapp.models.user:UserModel

# Colunas que o SEU UserModel adiciona — repetível, uma por coluna
tempest user create --email ana@example.com --password senha-forte-12 \
    --set display_name=Ana --set locale=pt-BR

# Promove/rebaixa um usuário existente (liga/desliga is_admin)
tempest user promote --email ana@example.com    # vira admin
tempest user revoke  --email ana@example.com    # volta a ser comum

# Troca a senha de um usuário existente (pede a senha duas vezes)
# e derruba as sessões dele junto
tempest user set-password --email ana@example.com

# Tabela de refresh token fora de src.db.models
tempest user set-password --email ana@example.com \
    --refresh-token-model myapp.models:UserRefreshTokenModel

# Troca a senha e deixa as sessões vivas (opt-out explícito)
tempest user set-password --email ana@example.com --keep-sessions

# Troca a senha e mantém as passkeys (opt-out explícito)
tempest user set-password --email ana@example.com --keep-passkeys

# Primeira conta privilegiada já com MFA + evento de auditoria na mesma transação
tempest user create --email staff@example.com --admin --require-mfa \
    --audit-model src.db.models:AuditLogModel --event staff.bootstrapped

# Conta que perdeu o celular e os recovery codes: remove o segundo fator,
# derruba as sessões e audita na mesma transação
tempest user mfa-reset --email staff@example.com \
    --audit-model src.db.models:AuditLogModel

# Lista
tempest user list                                # todos
tempest user list --admin                        # só admins
```

!!! info "Conta com MFA: `--require-mfa`, `set-password` e `mfa-reset`"
    `create --require-mfa` inscreve o segundo fator **antes** do commit:
    mostra a URI `otpauth://` e o segredo, pede o código do app
    Authenticator (até 3 tentativas) e só grava com código válido — sem ele,
    sai com código 1 e nenhuma linha é escrita. Exige `MFAMixin` no
    `UserModel` e a tabela de recovery codes (`--recovery-code-model`,
    default `src.db.models:UserRecoveryCodeModel`); os recovery codes saem
    uma vez, depois do commit. `--audit-model` (um `BaseAuditLogModel` com
    `AuditRequestMixin`) grava `record_event` na mesma transação, com o nome
    de `--event` (default `user.created`); sem ele, o comando avisa no stderr
    que nenhum evento foi gravado.

    `set-password` numa conta com MFA ativo (e `AUTH_MFA_ENABLED` ligado,
    como no login) pede o código — TOTP ou recovery code — e troca a senha
    por `UserAuthService.change_password_with_mfa`. Passo a passo na
    [receita de MFA](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/mfa.md#bootstrap-da-primeira-conta-privilegiada).

    Quem perdeu o celular **e** os recovery codes não tem código para dar:
    `mfa-reset` é a saída — veja
    [Recuperando quem perdeu o segundo fator](#mfa-reset) abaixo.

!!! tip "`UserModel` com coluna própria obrigatória: use `--set`"
    O `create` preenche `email`, `hashed_password`, `is_admin` e
    `is_active`. Toda **outra** coluna do seu `UserModel` entra por
    `--set <coluna>=<valor>`, validada contra as colunas mapeadas e
    convertida pro tipo da coluna (bool, int, float, `Decimal`, UUID,
    ISO-8601 de data/hora, enum pelo valor e JSON).

    ```bash
    tempest user create -e novato@example.com -p 'senha-forte-12' --no-admin
    # error: UserModel requires a value for: display_name.
    #        Pass each one as --set <column>=<value>.
    ```

    Coluna `NOT NULL` sem default que você não passou é **perguntada** num
    terminal interativo (uma pergunta por coluna) e vira erro com código 2
    numa execução sem TTY — em vez do `IntegrityError` cru que o banco
    levantava. Chave desconhecida também sai com 2, listando as colunas
    aceitas; `--set email=` / `--set hashed_password=` / `--set is_admin=`
    são recusadas apontando a flag dona (`--email`, `--password`,
    `--admin/--no-admin`).

    Insert recusado pelo banco (email duplicado, quase sempre) sai com
    código 1 e a mensagem do banco — `error: could not insert user: …` —
    em vez de traceback. Os valores do insert **não** aparecem nela: todo
    banco que o CLI abre usa `hide_parameters=True`, então o hash da senha
    que você acabou de digitar não vai parar no terminal nem no log da CI:

    ```text
    error: could not insert user: (sqlite3.IntegrityError) UNIQUE constraint failed: users.email
    [SQL: INSERT INTO users (email, hashed_password, ...) VALUES (?, ?, ...)]
    [SQL parameters hidden due to hide_parameters=True]
    ```

    Banco que ainda não foi migrado é detectado antes do insert, com
    código 1 e nenhuma linha escrita:

    ```text
    error: table(s) users not found in the database. Run `tempest db upgrade` first; nothing was written.
    ```

!!! tip "Sem `--admin`/`--no-admin`, o create pergunta"
    Quando você **não** passa nem `--admin` nem `--no-admin` num
    terminal interativo, o `tempest user create` pergunta
    `Should this user be an administrator? [y/N]`. Em execuções
    não-interativas (CI, pipes, scripts) o prompt é pulado e o usuário
    nasce comum (`is_admin=False`) — passe `--admin` explicitamente pra
    criar admin sem TTY.

`tempest user promote` / `tempest user revoke` localizam o usuário por email (case-insensitive) e só alternam `is_admin`. Quando nenhum usuário casa com o email, saem com código 1 e a mensagem `no user found`.

### Trocando a senha — `tempest user set-password`

É o lado do operador do fluxo de reset: a conta pra quem não dá pra mandar link, o primeiro admin trancado fora de um ambiente novo, a credencial rotacionada depois de um incidente. Localiza por email (case-insensitive), hasheia com o `set_password` do próprio model — o mesmo hash que o login verifica — e sai com código 1 e `no user found` quando ninguém casa.

```bash
tempest user set-password --email ana@example.com
# Password:
# Confirm:
# Password updated for ana@example.com (id=6f0c...)
# Revoked 2 active refresh token(s).
# Deleted 1 passkey(s).
```

!!! check "Trocar a senha derruba as sessões — por padrão"
    Trocar o hash, sozinho, não encerra sessão nenhuma: um access token
    já emitido continua válido até expirar, e um refresh token guardado
    no banco continua trocável por um novo — exatamente a janela que o
    reset existe pra fechar. Por isso a revogação **não é uma flag que
    você precisa lembrar**: o comando resolve
    `src.db.models:UserRefreshTokenModel` (o nome convencional da tabela
    opt-in de refresh token — o `tempest new` **não** a scaffolda; quem
    liga a tabela escreve o model com esse nome em `src/db/models/`) e revoga os refresh tokens do usuário **na mesma
    transação** que grava o hash novo.

    - Tabela em outro lugar? `--refresh-token-model myapp.models:UserRefreshTokenModel`.
      Spec que você digitou e não resolve é erro com código 2 — nunca
      degrada em silêncio pra "não revoguei nada".
    - Não quer revogar? `--keep-sessions`, que é recusado com código 2
      se vier junto de `--refresh-token-model` (as duas flags se
      contradizem).
    - Projeto que nunca ligou a tabela opt-in de refresh token não tem o
      que revogar, e o comando diz isso em `stderr`:
      `note: no refresh-token model at 'src.db.models:UserRefreshTokenModel', so no session was revoked. Pass --refresh-token-model if yours lives elsewhere.`

    A contagem é de sessões que **esta** execução matou — linha já
    revogada não é recontada, então rodar de novo imprime
    `Revoked 0 active refresh token(s).`

!!! check "E apaga as passkeys — também por padrão"
    A troca feita pelo operador é recuperação de conta, como o
    `mfa-reset`: uma passkey entra sem a senha, e a que foi cadastrada por
    quem segurou a conta sobreviveria ao hash novo. Então o comando apaga
    **todas** as passkeys do usuário na mesma transação
    (`UserAuthService.revoke_user_passkeys`), pelas mesmas regras das
    sessões: `--webauthn-credential-model` (default
    `src.db.models:UserWebAuthnCredentialModel`; sem tabela, avisa no
    stderr que nenhuma passkey foi apagada), `--keep-passkeys` como saída
    explícita, e as duas juntas recusadas com código 2. O dono cadastra a
    passkey de novo depois.

!!! info "Auditoria da troca: `--audit-model`"
    Como o `mfa-reset`, o `set-password` grava um evento quando recebe
    `--audit-model` (um `BaseAuditLogModel` com `AuditRequestMixin`):
    `user.password_set` por padrão, ou o nome de `--event`, com `actor="cli"` e
    `changes={"password_changed": true, "sessions_revoked": N,
    "passkeys_revoked": M}`, **na mesma transação** do hash novo — se a
    auditoria falha, a troca é desfeita. `--event` sem `--audit-model` sai
    com código 2.

    ```bash
    tempest user set-password --email ana@example.com \
        --audit-model src.db.models:AuditLogModel
    ```

!!! tip "O mesmo, de dentro do código"
    `await service.revoke_user_sessions(session, user_id=user.id)` é o
    par administrativo do `revoke_refresh_token`: logout-everywhere
    endereçado por id, pra quando você não tem o token em mãos (um
    endpoint de "desconectar tudo", um agente de suporte trancando conta
    comprometida). Devolve quantas linhas revogou, e `0` quando o
    projeto não usa `refresh_token_model` — JWT stateless não tem linha
    pra derrubar.

    ```python
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from tempest_fastapi_sdk import UserAuthService


    async def force_logout(
        user_id: UUID,
        session: AsyncSession,
        service: UserAuthService,
    ) -> int:
        """Revoga todas as sessões de um usuário. Retorna quantas caíram."""
        killed = await service.revoke_user_sessions(session, user_id=user_id)
        await session.commit()
        return killed
    ```

!!! info "A política de senha é a do seu projeto, não a da CLI"
    `create` e `set-password` validam o plaintext com
    `check_password_policy` contra os três campos `AUTH_PASSWORD_*` do
    `src.core.settings` do projeto — e contra os defaults do
    `PasswordPolicy` (mínimo 8 caracteres, máximo 72 **bytes**) quando o
    projeto não compõe `AuthSettings`. É a mesma régua do `signup` e do
    painel admin, então a senha que a CLI aceita é sempre uma que o dono
    da conta consegue usar pra logar.

    O teto é medido em **bytes UTF-8**, a unidade que o bcrypt conta: 40
    caracteres acentuados são 80 bytes e saem com código 2 e
    `error: password must be at most 72 bytes.` — antes de qualquer
    escrita no banco.

Resolução do `DATABASE_URL` igual ao `tempest db` (env var > instância ou factory de settings > `.env` > `alembic.ini`).

### Recuperando quem perdeu o segundo fator — `tempest user mfa-reset` { #mfa-reset }

Conta com MFA ativo cujo dono perdeu o celular **e** os recovery codes: o `set-password` pede o código, e não há código para dar. O `mfa-reset` remove o fator **sem prova** — por isso pede confirmação e grava quem fez.

```bash
tempest user mfa-reset --email staff@example.com \
    --audit-model src.db.models:AuditLogModel
# Remove the second factor of staff@example.com? It will log in with the password alone until it enrolls again. [y/N]: y
# MFA reset for staff@example.com (id=2ced...)
# Revoked 2 active refresh token(s).
# Deleted 1 passkey(s).

# Fora de um terminal (CI, script): --yes é obrigatório
tempest user mfa-reset --email staff@example.com --yes \
    --audit-model src.db.models:AuditLogModel --event support.mfa_recovered

# Remove o fator e deixa as sessões vivas (opt-out explícito)
tempest user mfa-reset --email staff@example.com --yes --keep-sessions
```

- Roda `UserAuthService.mfa_reset`: limpa `totp_secret`, `totp_enabled_at` e `totp_last_step` e apaga os recovery codes (`--recovery-code-model`, default `src.db.models:UserRecoveryCodeModel`; sem tabela, sai com código 2 nomeando a flag).
- **Revoga as sessões por padrão**, na mesma transação, com as mesmas regras do `set-password`: `--refresh-token-model` (default `src.db.models:UserRefreshTokenModel`), `--keep-sessions` como saída explícita, e as duas juntas recusadas com código 2.
- **Apaga as passkeys por padrão**, na mesma transação, pelas mesmas regras: `--webauthn-credential-model` (default `src.db.models:UserWebAuthnCredentialModel`; sem tabela, avisa no stderr que nenhuma passkey foi apagada), `--keep-passkeys` como saída explícita, e as duas juntas recusadas com código 2.
- `--audit-model` (com `AuditRequestMixin`) grava `record_event` na **mesma transação**, com o nome de `--event` (default `user.mfa_reset`); se a escrita da auditoria falha, o reset volta junto. Sem `--audit-model`, o comando avisa no stderr que nenhum evento foi gravado. `--event` sem `--audit-model` sai com código 2.
- Sem confirmação nada muda: fora de terminal, sem `--yes`, sai com código 1; no terminal, responder `n` também sai com 1.
- Conta **sem MFA ativo** sai com código 2 (`has no MFA active, nothing to reset.`); `UserModel` sem `MFAMixin` também, antes de abrir o banco. Email desconhecido sai com 1 e `no user found`.

Depois do reset a conta entra só com a senha até se inscrever de novo, e o `set-password` deixa de pedir código. A receita de MFA detalha o caminho, inclusive a chamada direta no código: [Conta que perdeu o segundo fator](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/mfa.md#mfa-reset).

### Inspecionar, desativar e apagar

O `list` responde "quem existe". As quatro abaixo respondem o resto.

```bash
# A linha inteira desse usuário, incluindo as colunas que o SEU model adiciona
tempest user show --email ana@example.com
tempest user show --email ana@example.com --json

# Desliga o acesso (reversível) e derruba as sessões junto
tempest user deactivate --email ana@example.com
tempest user activate --email ana@example.com

# Sessões (refresh tokens) desse usuário
tempest user sessions --email ana@example.com
tempest user sessions --email ana@example.com --revoke

# Apaga a linha (irreversível)
tempest user delete --email ana@example.com --yes
```

`show` **redige o `hashed_password`**: nenhuma pergunta que esse comando
responde precisa do hash, e imprimi-lo coloca uma credencial no scrollback
do terminal e no log do CI.

!!! warning "`deactivate` revoga sessão, e não é opcional"
    Virar `is_active=False` não encerra nada sozinho: o refresh token que já
    foi emitido continua trocável, e a conta que você acabou de desligar
    continua rendendo access token novo. Por isso a revogação roda na **mesma
    transação** que o flag — a mesma regra que o `set-password` segue.

`sessions` sem `--revoke` lista uma linha por token (id, família, criação,
expiração, estado). Usuário sem sessão imprime tabela vazia e sai com 0 —
"não está logado em lugar nenhum" é resposta, não erro. A contagem do
`--revoke` é de sessões que **aquela execução** matou, então rodar duas
vezes imprime `Revoked 0` na segunda.

!!! danger "`delete` é irreversível, e leva os tokens junto"
    A tabela de refresh token scaffoldada tem `ForeignKey` sem
    `ON DELETE CASCADE`, então apagar o usuário com token vivo falharia com
    erro de integridade citando uma constraint em vez da causa. O comando
    apaga os tokens na mesma transação. Fora de um terminal interativo,
    `--yes` é obrigatório. Quando a intenção é "essa pessoa não deve mais
    entrar", o comando certo é `deactivate`.

!!! note "`deactivate` bloqueia o login, não é banimento"
    Conta desativada não entra, mas o dono pode reativá-la sozinho: o
    `POST /auth/activation/request` manda um link de ativação novo para o
    e-mail da conta, e abrir esse link a deixa ativa de novo. É o
    comportamento pretendido — quem tem a caixa de e-mail volta. Para
    fechar a porta de vez, passe `refuses_email_link` ao `UserAuthService`
    com um predicado que case essa conta: ativação e reset por e-mail ficam
    recusados para ela (veja a [receita de MFA](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/recipes/mfa.md#email-link-refusal)).

## Segredos — `tempest secrets`

Quatro comandos, ordenados por quanto mexem no seu `.env`:

| Comando | Escreve no `.env`? | Quando usar |
| --- | --- | --- |
| `generate` | nunca | pegar um valor pra colar num secret manager |
| `init` | só o que está vazio ou placeholder | logo depois do `tempest new` |
| `rotate` | sempre, com backup | o segredo vazou ou envelheceu |
| `vapid` | par de chaves Web Push | ligar notificação no browser |

### Gerar um valor avulso — `tempest secrets generate`

Não toca em arquivo nenhum: imprime e pronto.

```bash
# Um segredo, valor cru
tempest secrets generate

# Três de uma vez
tempest secrets generate --count 3

# Rotulado, pronto pra colar num .env ou num painel de deploy
tempest secrets generate --keys JWT_SECRET,TOKEN_SECRET

# JSON, pra pipar num secret manager
tempest secrets generate --keys JWT_SECRET --json
```

Saída do terceiro:

```text
JWT_SECRET=_Ptx1Cq4lUAK4EJX7GAnXMp115d6i92I_rrTDJ3sS59noFLm3N1wTWL-F0OhCkbr
TOKEN_SECRET=qNsIyp8hp4BZll_BzcDS7omceBNB4a4EI9JVWvIQEg_R5VveTY0IuLBiogp0uRBa
```

!!! tip
    `--count` e `--keys` dizem a mesma coisa (quantos segredos sair), então
    passar os dois é erro de uso, não um deles vencendo em silêncio.

### Preencher o `.env` recém-criado — `tempest secrets init`

O `tempest new` escreve `JWT_SECRET=change-me-change-me-change-me-32` e
`TOKEN_SECRET=` vazio — exatamente os dois casos que o
`tempest check-config` reporta como `security.W001` / `security.W004`.
O `init` troca só esses:

```bash
tempest secrets init
```

```text
set JWT_SECRET (was placeholder)
set TOKEN_SECRET (was empty)
```

Chave que já tem valor de verdade é **mantida**, então rodar de novo não
faz nada:

```text
kept JWT_SECRET (already set)
kept TOKEN_SECRET (already set)
Nothing to do.
```

Isso é o que separa o `init` do `rotate`: o `init` é seguro de rodar em
qualquer momento — inclusive num script de setup — porque nunca destrói
segredo em uso. Pra substituir valor real, é opt-in explícito, e aí ele faz
backup antes:

```bash
tempest secrets init --force
```

### Rotacionar — `tempest secrets rotate`

Gera e reescreve as linhas correspondentes no `.env` **no lugar** — fazendo
backup do arquivo antigo antes — e deixa as outras linhas intactas.

```bash
# Rotaciona JWT_SECRET e TOKEN_SECRET no .env (gera .env.bak)
tempest secrets rotate

# Só imprime os novos valores (não escreve nada)
tempest secrets rotate --print

# Chaves e arquivo customizados
tempest secrets rotate --keys JWT_SECRET,SESSION_SECRET --env .env.prod

# Mais entropia, sem backup
tempest secrets rotate --length 64 --no-backup
```

!!! warning
    Rotacionar `JWT_SECRET` invalida todo token assinado com o valor antigo: usuários são deslogados e links de reset/ativação pendentes param de funcionar. Rotacione numa janela de manutenção e reinicie o serviço pra carregar os novos valores.

### Chaves Web Push — `tempest secrets vapid`

Web Push não usa string aleatória: usa um par de chaves P-256. O browser
assina a subscrição com a pública e o servidor assina cada push com a
privada, então os dois valores nascem juntos e não saem do `generate`.

```bash
# Escreve VAPID_PUBLIC_KEY + VAPID_PRIVATE_KEY no .env
tempest secrets vapid

# Com o contato que vai no claim `sub` do JWT VAPID
tempest secrets vapid --subject mailto:ops@example.com

# Só imprime
tempest secrets vapid --print
```

São exatamente os campos que o `WebPushSettings` lê, no formato que o
`pywebpush` lê de volta: privada é o escalar de 32 bytes e pública é o
ponto não-comprimido de 65 bytes, ambos em base64url sem padding.

!!! danger
    Trocar um par que já existe **invalida toda subscrição ativa**: o browser
    que subscreveu com a chave pública antiga não aceita push assinado com a
    nova, e cada cliente precisa subscrever de novo. Por isso o comando se
    recusa a sobrescrever sem `--force`.

### Recap

- `generate` imprime, `init` preenche o que falta, `rotate` troca tudo,
  `vapid` gera o par do Web Push.
- `init` é idempotente e nunca perde segredo em uso — é o comando do
  primeiro dia do projeto.
- `rotate` e `vapid --force` são destrutivos de formas diferentes: um
  desloga usuário, o outro derruba subscrição de push.
- Todo arquivo que esses comandos escrevem sai `0600`.
