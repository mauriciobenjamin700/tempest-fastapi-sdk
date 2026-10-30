"""English (``en-US``) OpenAPI text of the routes ``make_auth_router`` mounts.

Every string here is the one the router carried inline before #643, moved
verbatim so the English document did not change by a byte.
"""

from __future__ import annotations

from tempest_fastapi_sdk.api.route_docs.doc import RouteDoc

AUTH_ROUTES: dict[str, RouteDoc] = {
    "auth.activate": RouteDoc(
        summary=("Activate an account from the emailed token (JSON)"),
        description=(
            "Consume a single-use **activation token** (the one sent in "
            "the signup email or returned by ``/signup``) and flip the "
            "account to active.\n\n"
            "This is the **JSON / SPA** variant: a frontend reads the "
            "``{token}`` from the activation URL and POSTs it here. On "
            "success the account is activated **and logged in** — the "
            "response carries a fresh ``access_token`` + "
            "``refresh_token`` pair so the user never has to type their "
            "password again right after confirming.\n\n"
            "**MFA.** An activation token normally belongs to an account "
            "that just signed up, so there is no factor to prove. It can "
            "also be issued by ``/activation/request`` for a deactivated "
            "account, and that account may have TOTP enrolled. In that "
            "case — and only then — the route answers like ``/login``: "
            "``mfa_required=true`` plus a short-lived ``mfa_token`` "
            "instead of the pair, exchanged at ``/mfa/verify``.\n\n"
            "The token is rejected with **400** when it is unknown, "
            "already used, or past its ``AUTH_ACTIVATION_TTL_SECONDS`` "
            "expiry.\n\n"
            "!!! note\n"
            "    When ``AUTH_BACKEND_LINKS=True`` the SDK also mounts a "
            "    **GET** ``/activate/{token}`` that renders an HTML page "
            "    directly from the backend — use that one when you have "
            "    no frontend. This POST endpoint is always available."
        ),
    ),
    "auth.login": RouteDoc(
        summary=("Log in with email + password → JWT pair"),
        description=(
            "Authenticate an **active** user with their email and "
            "password and receive a JWT ``access_token`` + "
            "``refresh_token`` pair.\n\n"
            "Returns **401** for wrong credentials and for accounts that "
            "exist but were never activated — the message is "
            "intentionally generic so callers can't tell which case it "
            "was. With ``AUTH_REVEAL_INACTIVE_ACCOUNT=True``, the right "
            "password on a deactivated account returns **403** "
            "``ACCOUNT_INACTIVE`` instead.\n\n"
            "**MFA.** When the user has finished TOTP enrollment "
            "(and ``AUTH_MFA_ENABLED=True``) this endpoint does *not* "
            "return the JWT pair. Instead it returns "
            "``mfa_required=true`` plus a short-lived ``mfa_token``; "
            "exchange that token for the real JWT pair at "
            "``/mfa/verify``.\n\n"
            "**Rate limit.** By default the 6th wrong password for the "
            "same e-mail inside 15 minutes — and every attempt after "
            "it, right password included — returns **429** "
            "``TOO_MANY_REQUESTS`` with ``Retry-After``; a successful "
            "login clears the count. The same happens for an e-mail "
            "with no account, so the 429 reveals nothing about which "
            "e-mails exist. ``login_ip_throttle`` adds a per-client "
            "budget."
        ),
    ),
    "auth.activation_request": RouteDoc(
        summary=("Re-send the activation link (no credentials needed)"),
        description=(
            "Issue a fresh activation link for an account that never "
            "activated — the signup email bounced, went to spam, or the "
            "tab was closed.\n\n"
            "**No authentication**, by necessity: logging in is refused "
            "while the account is inactive, so a route behind a bearer "
            "token could not help the person it exists for.\n\n"
            "**Always returns 202** with the same generic message for an "
            "unknown address, an already-active account and a genuine "
            "re-send, so probing it cannot enumerate accounts. An active "
            "account is not re-mailed: the link would authorize nothing. "
            "A mail the SMTP server does not take still answers **202**: "
            "the failure is logged server-side and no token is stored."
        ),
    ),
    "auth.password_reset_request": RouteDoc(
        summary=("Start a password reset (request a reset link)"),
        description=(
            "Kick off the forgot-password flow for the given email.\n\n"
            "**Always returns 202** with the same generic message "
            "whether or not the email matches a real account — this "
            "prevents attackers from enumerating which emails are "
            "registered by probing this endpoint. That includes a reset "
            "mail the SMTP server does not take: it is logged "
            "server-side, no token is stored, and the answer stays "
            "**202**.\n\n"
            "When a matching account exists a single-use reset token is "
            "issued (valid for ``AUTH_PASSWORD_RESET_TTL_SECONDS``). "
            "When ``EmailUtils`` is wired the reset link is **emailed** "
            "(localized via ``AUTH_DEFAULT_LOCALE``) and ``reset_url`` "
            "stays ``null`` in the body; when email is not configured — "
            "or ``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` — the "
            "ready-to-use ``reset_url`` is returned in the body so you "
            "can drive the flow without SMTP."
        ),
    ),
    "auth.password_reset_confirm": RouteDoc(
        summary=("Finish a password reset (set the new password) — JSON"),
        description=(
            "Complete the forgot-password flow: consume the single-use "
            "reset token and store the new password.\n\n"
            "This is the **JSON / SPA** variant — a frontend collects "
            "the new password and POSTs it together with the ``token`` "
            "read from the reset URL. The new password must satisfy "
            "``AUTH_PASSWORD_MIN_LENGTH`` and the complexity rules; "
            "violations return **422**. A token that is unknown, "
            "already used, or expired returns **400**.\n\n"
            "On success the password is updated **and the user is "
            "logged in** — the response carries a fresh "
            "``access_token`` + ``refresh_token`` pair.\n\n"
            "**MFA.** When the account has finished TOTP enrollment "
            "(and ``AUTH_MFA_ENABLED=True``) the reset does *not* "
            "return the JWT pair, exactly like ``/login``. A stolen "
            "mailbox would otherwise be enough to walk past the second "
            "factor — the very scenario MFA exists for. Instead the "
            "response carries ``mfa_required=true`` plus a short-lived "
            "``mfa_token``; exchange it for the pair at ``/mfa/verify``.\n\n"
            "**Account recovery.** In the same transaction every stored "
            "session of the user is revoked and, when WebAuthn is wired, "
            "every "
            "passkey of the user is deleted — one registered while "
            "somebody else held the account would otherwise keep signing "
            "in. The owner registers their passkeys again.\n\n"
            "!!! note\n"
            "    When ``AUTH_BACKEND_LINKS=True`` the SDK also mounts "
            "    **GET/POST** ``/password-reset/{token}`` that render a "
            "    self-contained HTML form + result page from the "
            "    backend — use those when you have no frontend."
        ),
    ),
    "auth.me": RouteDoc(
        summary=("Return the authenticated account"),
        description=(
            "Resolve the bearer ``access_token`` to the account that "
            "owns it.\n\n"
            "Two jobs in one endpoint: it tells the client **who** is "
            "logged in without the client caching a profile, and it says "
            "whether a stored token is **still valid** — a **401** is the "
            "signal to call ``/refresh`` before sending the user back to "
            "the login screen.\n\n"
            "The response never carries the password hash. FastAPI "
            "serializes through the response model, so only declared "
            "fields reach the wire; a project that wants extra columns "
            "subclasses ``AuthUserSchema`` and passes it as "
            "``me_response_model``.\n\n"
            "Returns **404** when the token is well-formed and unexpired "
            "but the account behind it no longer exists."
        ),
    ),
    "auth.password_change": RouteDoc(
        summary=("Change your own password (while logged in)"),
        description=(
            "Rotate the **currently authenticated** user's password "
            "(requires a valid bearer ``access_token``).\n\n"
            "Unlike the forgot-password flow there is **no token** — the "
            "user is already logged in. They must re-enter their "
            "``current_password`` to confirm ownership; a mismatch "
            "returns **401**. The ``new_password`` must satisfy "
            "``AUTH_PASSWORD_MIN_LENGTH`` (and the complexity rules when "
            "``AUTH_PASSWORD_REQUIRE_COMPLEXITY=True``); violations return "
            "**422**.\n\n"
            "**Every session ends**, the caller's own refresh token "
            "included: the old password may be known to someone else. The "
            "response is **200** and is what ``/auth/login`` would answer "
            "for the proofs in this request:\n\n"
            "- **Account without MFA:** a fresh pair — in the body for a "
            "bearer caller, in cookies for a caller that authenticated with "
            "the access cookie.\n"
            "- **Account with MFA, no ``code``:** the password changes and "
            "the response is the login challenge (``mfa_required=true`` and "
            "an ``mfa_token`` for ``POST /auth/mfa/verify``), with no pair.\n"
            "- **Account with MFA and a ``code``** (TOTP or recovery code): "
            "the code is checked and spent **before** the change; a wrong "
            "one answers **401** and nothing changes. A right one changes "
            "the password and returns a pair with an MFA ``amr``.\n\n"
            "The current access token's ``amr`` never carries a second "
            "factor into the new pair. The old access token lives until it "
            "expires; discard it."
        ),
    ),
    "auth.email_change_request": RouteDoc(
        summary=("Start an email change (while logged in)"),
        description=(
            "Stage a move to a new email for the **currently "
            "authenticated** user (requires a valid bearer "
            "``access_token``). The user re-enters ``current_password`` "
            "to confirm ownership; a mismatch returns **401**, an "
            "already-taken address returns **409**.\n\n"
            "A single-use confirmation link is sent to the **new** "
            "address (valid for ``AUTH_EMAIL_CHANGE_TTL_SECONDS``); the "
            "account email only changes once that link is confirmed via "
            "``POST /auth/email-change/confirm``. When "
            "``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` or the ``[email]`` "
            "extra is absent, the ready-to-use ``confirm_url`` is "
            "returned in the body instead of emailed. A confirmation mail "
            "the SMTP server does not take answers **503** "
            "``EMAIL_UNAVAILABLE`` and stores no token."
        ),
    ),
    "auth.email_change_confirm": RouteDoc(
        summary=("Finish an email change (apply the new address) — JSON"),
        description=(
            "Consume the single-use token from the confirmation link and "
            "flip the account email to the staged address. An unknown, "
            "already-used or expired token returns **400**; a target "
            "address taken in the meantime returns **409**.\n\n"
            "When ``AUTH_EMAIL_CHANGE_NOTIFY_OLD=True`` a security notice "
            "is sent to the previous address on success; when that notice "
            "cannot be sent the answer is **503** ``EMAIL_UNAVAILABLE``, "
            "the address is not changed and the link stays valid.\n\n"
            "!!! note\n"
            "    When ``AUTH_BACKEND_LINKS=True`` the SDK also mounts "
            "    **GET** ``/email-change/{token}`` that renders a "
            "    self-contained HTML result page from the backend."
        ),
    ),
    "auth.email_verify_request": RouteDoc(
        summary=("Re-send a verification link for your current email"),
        description=(
            "Issue a fresh verification link for the **currently "
            "authenticated** user's existing email (requires a valid "
            "bearer ``access_token``). No address change — confirming the "
            "link marks the account active. Useful when the original "
            "activation email was lost. The link is emailed, or returned "
            "as ``confirm_url`` when ``AUTH_RETURN_TOKEN_IN_RESPONSE=True``. "
            "A mail the SMTP server does not take answers **503** "
            "``EMAIL_UNAVAILABLE`` and stores no token."
        ),
    ),
    "auth.email_verify_confirm": RouteDoc(
        summary=("Confirm your current email (mark the account verified) — JSON"),
        description=(
            "Consume the single-use token from the verification link and "
            "mark the account active. An unknown, already-used or expired "
            "token returns **400**."
        ),
    ),
    "auth.email_recovery_request": RouteDoc(
        summary=("Recover an account whose mailbox is no longer accessible"),
        description=(
            "**Unauthenticated** entry point for a user who lost access "
            "to their email. Mounted only when "
            "``AUTH_EMAIL_RECOVERY_ENABLED=True``.\n\n"
            "The account is located by its current (old) ``email``; "
            "identity is proven by ``current_password`` and — when the "
            "account has MFA enrolled — a valid ``mfa_code``. On success "
            "a confirmation link is sent to the **new** address and a "
            "security notice to the old one.\n\n"
            "**Always returns 202** with the same generic message for "
            "every soft failure (unknown email, wrong password, "
            "missing/invalid MFA code) so the endpoint can't be used to "
            "enumerate accounts. Once identity is proven, a confirmation "
            "mail the SMTP server does not take answers **503** "
            "``EMAIL_UNAVAILABLE`` (like a taken ``new_email`` answers "
            "**409**) and stores no token."
        ),
    ),
    "auth.refresh": RouteDoc(
        summary=("Exchange a refresh token for a fresh JWT pair"),
        description=(
            "Mint a brand-new ``access_token`` + ``refresh_token`` pair "
            "from a valid **refresh token** — no email or password "
            "required. This is how a client keeps a session alive once "
            "the short-lived ``access_token`` expires: replay the "
            "long-lived ``refresh_token`` here instead of forcing the "
            "user to log in again.\n\n"
            "A stolen *access* token replayed here is rejected with "
            "**401**. An expired, malformed, revoked, or wrongly-signed "
            "token also returns **401**, and an inactive account returns "
            "**403**.\n\n"
            "!!! warning\n"
            "    Both tokens **rotate**: the response carries a new "
            "    refresh token. Persist that one and discard the token "
            "    you sent.\n\n"
            '!!! info "Stateless vs DB-backed"\n'
            "    When the service is wired with a ``refresh_token_model`` "
            "    the refresh token is an **opaque, single-use** value: the "
            "    presented token is invalidated on rotation, replaying an "
            "    already-rotated token is treated as theft and **revokes "
            "    the whole token family** (401), and ``POST /auth/logout`` "
            "    can revoke a session early. Without that model the SDK "
            "    falls back to a stateless JWT refresh token — the old "
            "    token stays valid until its own expiry and cannot be "
            "    revoked."
        ),
    ),
    "auth.signup": RouteDoc(
        summary=("Register a new account (email + password)"),
        description=(
            "Create a brand-new user from an email, a password and an "
            "optional display name.\n\n"
            "**Password policy.** The password must satisfy "
            "``AUTH_PASSWORD_MIN_LENGTH`` (and the character-complexity "
            "rules when ``AUTH_PASSWORD_REQUIRE_COMPLEXITY=True``); "
            "violations return **422**. A duplicate email returns "
            "**409**.\n\n"
            "**What happens next depends on ``AUTH_AUTO_ACTIVATE``:**\n\n"
            "* ``AUTH_AUTO_ACTIVATE=True`` — the account is active "
            "immediately and the **201** response already carries the "
            "``access_token`` + ``refresh_token`` JWT pair "
            "(``activation_required=false``).\n"
            "* ``AUTH_AUTO_ACTIVATE=False`` (default) — the account "
            "starts inactive and an activation token is issued "
            "(``activation_required=true``). When ``EmailUtils`` is "
            "wired the activation link is **emailed** (localized via "
            "``AUTH_DEFAULT_LOCALE``) and ``activation_url`` is "
            "``null``; when email is not configured — or "
            "``AUTH_RETURN_TOKEN_IN_RESPONSE=True`` — the ready-to-use "
            "``activation_url`` is returned in the body instead so you "
            "can complete activation without SMTP.\n\n"
            "**SMTP failure.** When the activation mail cannot be "
            "sent, the response is **503** ``EMAIL_UNAVAILABLE`` and "
            "no account is created — the same address can sign up "
            "again once the server is back.\n\n"
            "**Rate limit.** With ``signup_throttle`` configured, the "
            "attempts past its budget for one client IP return **429** "
            "``TOO_MANY_REQUESTS`` with ``Retry-After``."
        ),
    ),
    "auth.logout": RouteDoc(
        summary=("Revoke a refresh token (logout)"),
        description=(
            "Revoke a **DB-backed refresh token** so it can no longer "
            "be exchanged at ``POST /auth/refresh``, even before its "
            "natural expiry.\n\n"
            "By default only the presented token's rotation **family** "
            "(the lineage descended from one login) is revoked — so a "
            "descendant token a thief may hold dies too. Pass "
            "``all_sessions=true`` to revoke **every** active refresh "
            "token the user owns (log out everywhere).\n\n"
            "The endpoint is **idempotent**: an unknown / already-"
            "revoked token still returns **204** and never leaks "
            "whether the token existed.\n\n"
            "!!! note\n"
            "    Mounted only when the service is wired with a "
            "    ``refresh_token_model``. Stateless JWT refresh tokens "
            "    cannot be revoked, so the endpoint is absent in that "
            "    mode."
        ),
    ),
    "auth.login_cookie": RouteDoc(
        summary=("Log in — set the JWT pair as HttpOnly cookies"),
        description=(
            "Same credentials check as ``POST /auth/login``, but the "
            "``access_token`` / ``refresh_token`` are returned as "
            "``HttpOnly`` cookies instead of in the body — the body "
            "keeps them ``null``. The MFA branch is unchanged: when a "
            "second factor is required the response still carries "
            "``mfa_required=true`` + ``mfa_token`` and sets no session "
            "cookies. Shares the login throttles with ``POST /auth/login``"
            ": the same e-mail budget, the same **429**."
        ),
    ),
    "auth.refresh_cookie": RouteDoc(
        summary=("Rotate the JWT pair from the refresh cookie"),
        description=(
            "Mint a fresh ``access_token`` / ``refresh_token`` pair "
            "from the **refresh cookie** (no body required) and set "
            "the rotated pair back as cookies. Returns **401** when "
            "the refresh cookie is missing, expired, revoked or is "
            "actually an access token; **403** for an inactive "
            "account."
        ),
    ),
    "auth.logout_cookie": RouteDoc(
        summary=("Clear the auth cookies (and revoke the refresh token)"),
        description=(
            "Delete both auth cookies. When the service is wired with "
            "a ``refresh_token_model`` and a refresh cookie is "
            "present, its rotation **family** is revoked too so the "
            "session dies server-side. Always returns **204**, even "
            "when no cookie was set (idempotent)."
        ),
    ),
    "auth.mfa_enroll": RouteDoc(
        summary=("Begin TOTP enrollment (returns secret + recovery codes once)"),
        description=(
            "Start two-factor enrollment for the **currently "
            "authenticated** user (requires a valid bearer "
            "``access_token``).\n\n"
            "Generates a fresh TOTP ``secret`` and a "
            "``provisioning_uri`` you render as a QR code for an "
            "authenticator app (Google Authenticator, 1Password, "
            "etc.), plus a batch of one-time ``recovery_codes``.\n\n"
            "!!! warning\n"
            "    The secret and recovery codes are returned **only "
            "    this once** and are never retrievable again — show "
            "    them to the user immediately. Enrollment is not "
            "    active until confirmed at ``/mfa/confirm``.\n\n"
            "Once MFA is active this answers **409** with code "
            "``MFA_ALREADY_ENROLLED`` and changes nothing: a bearer "
            "token alone must not reset the second factor. To rotate "
            "the secret, call ``/mfa/disable`` (password + code) and "
            "enroll again. Calling it again while an enrollment is "
            "still pending replaces the staged secret and codes."
        ),
    ),
    "auth.mfa_confirm": RouteDoc(
        summary=("Finish TOTP enrollment (verify the first code)"),
        description=(
            "Activate the TOTP enrollment started at "
            "``/mfa/enroll`` for the **currently authenticated** "
            "user.\n\n"
            "Submit the 6-digit ``code`` currently shown by the "
            "authenticator app — this proves the shared secret was "
            "stored correctly. A wrong or expired code returns "
            "**401** and enrollment stays inactive. On success "
            "every subsequent ``/login`` for this user becomes a "
            "two-step flow.\n\n"
            "**Every other session ends.** Confirming revokes all "
            "refresh tokens the user holds, so a session opened "
            "with the password alone cannot keep refreshing past "
            "the new factor. The response carries a fresh pair "
            'whose access token has ``amr == ["pwd", "otp"]``, '
            "so the device that confirmed stays signed in: in the "
            "body for a bearer caller, as cookies for a caller "
            "authenticated by the access cookie."
        ),
    ),
    "auth.mfa_disable": RouteDoc(
        summary=("Turn off TOTP for the current user"),
        description=(
            "Remove two-factor authentication from the **currently "
            "authenticated** user and delete their stored recovery "
            "codes.\n\n"
            "Re-authentication is required: the caller must supply "
            "both the account ``password`` **and** a currently-valid "
            "TOTP ``code`` (or recovery code). Either one wrong "
            "returns **401** and MFA stays enabled. On success the "
            "endpoint returns **204** and ``/login`` goes back to a "
            "single step."
        ),
    ),
    "auth.mfa_verify": RouteDoc(
        summary=("Complete a two-step login (exchange mfa_token + code)"),
        description=(
            "Second and final step of an MFA-protected login.\n\n"
            "When ``/login`` returns ``mfa_required=true`` it hands "
            "back a short-lived ``mfa_token``. POST that token here "
            "together with the current 6-digit TOTP ``code`` (or a "
            "one-time recovery code). On success the endpoint "
            "returns the real ``access_token`` + ``refresh_token`` "
            "pair, finishing the login.\n\n"
            "A wrong / expired ``code`` or ``mfa_token`` returns "
            "**401**. A TOTP code is accepted once: replaying it, "
            "even inside its 30-second window, is a wrong code. "
            "Too many wrong codes for the same account return "
            "**429** with ``Retry-After`` until the window passes, "
            "even when the next code is right. This endpoint needs "
            "**no** bearer token — the ``mfa_token`` itself is the "
            "proof that step one succeeded."
        ),
    ),
    "auth.webauthn_register_begin": RouteDoc(
        summary=("Start registering a passkey / security key"),
        description=(
            "First half of the registration ceremony, for the "
            "**currently authenticated** user (requires a valid "
            "bearer ``access_token``).\n\n"
            "The token alone is not enough: the body carries "
            "``current_password`` and, when the account has MFA "
            "enrolled, a TOTP or recovery ``code``. A passkey signs "
            "in without either, so registering one asks for the same "
            "proof as disabling MFA. A wrong password or code "
            "returns **401**.\n\n"
            "Pass the returned ``options.publicKey`` to "
            "``navigator.credentials.create()`` and post what it "
            "returns to ``/webauthn/register/complete``, along with "
            "the ``challenge_id`` from this response.\n\n"
            "Credentials the account already holds are sent as "
            "``excludeCredentials``, so registering the same "
            "authenticator twice is refused by the device instead "
            "of creating a duplicate nobody can tell apart."
        ),
    ),
    "auth.webauthn_register_complete": RouteDoc(
        summary=("Finish registering a passkey / security key"),
        description=(
            "Second half of the registration ceremony. Verifies the "
            "attestation against the challenge issued by "
            "``/webauthn/register/begin`` and stores the public "
            "key.\n\n"
            "The challenge is consumed here, so a captured response "
            "cannot be replayed. A wrong, expired or already-used "
            "``challenge_id`` returns **401**; an authenticator that "
            "is already registered returns **422**."
        ),
    ),
    "auth.webauthn_authenticate_begin": RouteDoc(
        summary=("Start a passwordless login"),
        description=(
            "First half of the login ceremony. Needs **no** bearer "
            "token — this is how a session starts.\n\n"
            "Omit ``email`` for the usernameless flow: the options "
            "carry no credential list and the authenticator offers "
            "the accounts it stores. Pass ``email`` to narrow the "
            "ceremony to one account, which helps an authenticator "
            "that keeps no discoverable credential.\n\n"
            "An unknown address returns a normal ceremony with an "
            "empty credential list — answering differently would "
            "turn this endpoint into an account-enumeration oracle."
        ),
    ),
    "auth.webauthn_authenticate_complete": RouteDoc(
        summary=("Complete a passwordless login (returns the JWT pair)"),
        description=(
            "Second half of the login ceremony. Verifies the "
            "assertion and returns the ``access_token`` + "
            "``refresh_token`` pair.\n\n"
            "A bad or replayed challenge, an unknown credential, a "
            "failed signature, an authenticator whose signature "
            "counter did not advance (the spec's cloned-device "
            "signal) or an inactive account all return **401**.\n\n"
            "MFA does not gate this endpoint: a passkey with user "
            "verification already proves possession *and* a local "
            "factor, which is what the second step exists for."
        ),
    ),
    "auth.webauthn_list_credentials": RouteDoc(
        summary=("List the current user's registered authenticators"),
        description=(
            "Every passkey / security key the **currently "
            "authenticated** user registered, oldest first. Returns "
            "``200`` with an empty list when there are none.\n\n"
            "``backed_up`` tells a device-bound credential (lost "
            "with the device) from a synced one, which is what "
            "decides whether the account still needs a recovery "
            "path."
        ),
    ),
    "auth.webauthn_delete_credential": RouteDoc(
        summary=("Remove one of the current user's authenticators"),
        description=(
            "Deletes a credential belonging to the **currently "
            "authenticated** user. The lookup is scoped to the "
            "caller, so an ID registered by somebody else answers "
            "**404** exactly like one that does not exist.\n\n"
            "Removing the last passkey leaves the account on its "
            "other factors — the endpoint does not check that one "
            "remains, because whether a password is still an "
            "acceptable fallback is the application's decision."
        ),
    ),
    "auth.oauth_login": RouteDoc(
        summary=("Start the social-login redirect"),
        description=(
            "Mints a CSRF ``state``, stores it in an ``HttpOnly`` "
            "cookie scoped to the auth prefix, and redirects the "
            "browser to the provider's consent screen with **302 "
            "Found**.\n\n"
            "Point a link or a button straight at this URL — it is "
            "a navigation, not an XHR: the response is a redirect "
            "to a different origin, so `fetch` will either follow "
            "it opaquely or fail CORS.\n\n"
            "The state cookie is always written with "
            "``SameSite=Lax``, whatever ``AUTH_COOKIE_SAMESITE`` "
            "says, because the provider sends the user back with a "
            "**cross-site top-level navigation** — ``Strict`` would "
            "withhold the cookie there and every login would fail "
            "the state check it exists to pass. It expires after "
            "``AUTH_OAUTH_STATE_TTL_SECONDS`` and carries the "
            "provider key alongside the random value, so a state "
            "minted for one provider cannot be replayed at "
            "another's callback.\n\n"
            "An unregistered ``{provider}`` answers **404**."
        ),
    ),
    "auth.oauth_callback": RouteDoc(
        summary=("Finish the social login and return the JWT pair"),
        description=(
            "Where the provider sends the user back. Validates the "
            "``state`` against the cookie, exchanges the ``code`` "
            "for tokens, resolves the identity to a local account "
            "and answers with the **same JWT pair** "
            "``POST /auth/login`` returns — same ``typ`` claim, "
            "same opaque refresh token, same rotation family, same "
            "``POST /auth/logout``.\n\n"
            "**Delivery follows ``AUTH_TOKEN_DELIVERY``**, with one "
            "difference from the rest of the router: ``both`` sets "
            "the cookies *and* fills the body, instead of mounting "
            "a second endpoint. The callback URL is registered at "
            "the provider, so a parallel ``/auth/cookie/...`` route "
            "would force every provider console to hold two "
            "redirect URIs for one flow.\n\n"
            "Answers **401** when the state is missing or does not "
            "match (a forged callback), or when the provider "
            "reported an ``error``; **422** when the provider "
            "returned no email, since the column is ``NOT NULL "
            "UNIQUE`` and there is nothing to store; **409** when "
            "the email already belongs to another account and "
            "``AUTH_OAUTH_LINK_BY_VERIFIED_EMAIL`` does not allow "
            "attaching it; **403** when the identity is new and "
            "account creation is off; **404** for an unregistered "
            "provider."
        ),
    ),
    "auth.oauth_token": RouteDoc(
        summary=("Log in with a provider token obtained on the device"),
        description=(
            "The **token-in-hand** half of social login, for clients "
            "that have no browser to redirect: a native app runs the "
            "provider's own SDK, ends up holding an access token, and "
            "posts it here.\n\n"
            "The session it returns is the one "
            "``/oauth/{provider}/callback`` returns — same claims, "
            "same refresh family, same ``POST /auth/logout``.\n\n"
            "**The token is checked against this application before "
            "anything is looked up.** A provider's userinfo endpoint "
            "answers *whose* token this is, never *who it was issued "
            "for* — so without that check, any app that walked the "
            "victim through a consent screen would hold a token that "
            "resolves to the victim here, and posting it would hand "
            "over the victim's session. A token minted for another "
            "``client_id`` is refused with **401** "
            "``OAUTH_TOKEN_AUDIENCE_MISMATCH``; a provider that "
            "cannot answer the question at all makes this route "
            "refuse with **501** ``OAUTH_AUDIENCE_UNVERIFIABLE`` "
            "rather than trust the token.\n\n"
            "There is no ``state`` to compare — nothing was "
            "redirected — and none is needed: the caller supplies "
            "the credential in the body, so there is no browser "
            "navigation for a third-party page to forge.\n\n"
            "Send the token in the **body**. A URL reaches the "
            "access log, the browser history and every ``Referer`` "
            "header on the way, and this value is live at the "
            "provider."
        ),
    ),
    "auth.oauth_list_accounts": RouteDoc(
        summary=("List the identities linked to the current account"),
        description=(
            "Every provider the **currently authenticated** user "
            "has linked, oldest first. Returns **200** with an "
            "empty list for an account that only ever used a "
            "password.\n\n"
            "``email_verified`` is three-valued on purpose: "
            "``null`` means the provider said nothing about the "
            "address, which is not the same as saying it is "
            "unverified."
        ),
    ),
    "auth.oauth_unlink_account": RouteDoc(
        summary=("Detach a provider from the current account"),
        description=(
            "Removes the link belonging to the **currently "
            "authenticated** user. The lookup is scoped to the "
            "caller, so a provider linked by somebody else answers "
            "**404** exactly like one that was never linked.\n\n"
            "The account keeps its password and its other links, "
            "so it stays reachable. Unlinking the only provider on "
            "an account created by a callback leaves "
            "``POST /auth/password-reset/request`` as the way back "
            "in — the same door that flow always offered, since "
            "the email is already on the row."
        ),
    ),
}
"""Operation text of the auth routes, keyed ``auth.<endpoint name>``."""

AUTH_TEXTS: dict[str, str] = {
    "auth.login.429": (
        "Too many failed logins for this e-mail or this client "
        "— retry after ``Retry-After`` seconds."
    ),
    "auth.signup.429": (
        "Too many signups from this client — retry after ``Retry-After`` seconds."
    ),
    "auth.mfa_enroll.409": "MFA is already active for this account.",
}
"""Response descriptions declared on the auth routes."""

__all__: list[str] = ["AUTH_ROUTES", "AUTH_TEXTS"]
