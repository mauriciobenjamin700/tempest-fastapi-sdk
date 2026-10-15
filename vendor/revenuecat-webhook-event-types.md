---
id: "integrations/webhooks/event-types-and-fields"
title: "Event Types and Fields"
description: "RevenueCat sends webhooks in response to events that occur in your app. This page defines those event types and the data contained in each webhook."
permalink: "/docs/integrations/webhooks/event-types-and-fields"
slug: "event-types-and-fields"
version: "current"
original_source: "docs/integrations/webhooks/event-types-and-fields.mdx"
---

> **AI agents:** This is the Markdown version of a RevenueCat documentation page. For the complete documentation index, see [llms.txt](https://www.revenuecat.com/docs/llms.txt).

RevenueCat sends webhooks in response to events that occur in your app. This page defines those event types and the data contained in each webhook.

For sample payloads, see [Sample webhook events](https://www.revenuecat.com/docs/integrations/webhooks/sample-events).

## Events format

Webhook events are serialized in JSON. The body of a `POST` request to your server will contain the serialized event, as well as the API version.

```json
{
  "api_version": "1.0",
  "event": {
    "aliases": [
      "yourCustomerAliasedID",
      "yourCustomerAliasedID"
    ],
    "app_id": "yourAppID",
    "app_user_id": "yourCustomerAppUserID",
    "commission_percentage": 0.3,
    "country_code": "US",
    "currency": "USD",
    "entitlement_id": "pro_cat",
    "entitlement_ids": [
      "pro_cat"
    ],
    "environment": "PRODUCTION",
    "event_timestamp_ms": 1591121855319,
    "expiration_at_ms": 1591726653000,
    "id": "UniqueIdentifierOfEvent",
    "is_family_share": false,
    "offer_code": "free_month",
    "original_app_user_id": "OriginalAppUserID",
    "original_transaction_id": "1530648507000",
    "period_type": "NORMAL",
    "presented_offering_id": "OfferingID",
    "price": 2.49,
    "price_in_purchased_currency": 2.49,
    "product_id": "onemonth_no_trial",
    "purchased_at_ms": 1591121853000,
    "store": "APP_STORE",
    "subscriber_attributes": {
      "$Favorite Cat": {
        "updated_at_ms": 1581121853000,
        "value": "Garfield"
      }
    },
    "takehome_percentage": 0.7,
    "tax_percentage": 0.3,
    "transaction_id": "170000869511114",
    "type": "INITIAL_PURCHASE"
  }
}
```

## Event types

Each group of events below links to its [field descriptions](#fields), lists its event descriptions, and then displays a table showing store compatibility or feature availability (`✅` = supported, `❌` = not supported).

### Dashboard test

**Applicable fields:** [Common fields](#common-fields) · [Subscriber identity](#subscriber-identity-fields) · [Subscription lifecycle](#subscription-lifecycle-fields)

- **`TEST`** — RevenueCat issued a test event.
  - This event uses a purchase-like sample payload and isn't persisted in production.

| Webhook Event Type | App Store | Google Play | Amazon | Stripe | Promo | Roku | RevenueCat Billing |
| ------------------ | --------- | ----------- | ------ | ------ | ----- | ---- | ------------------ |
| `TEST`             | ✅        | ✅          | ❌     | ❌     | ❌    | ❌   | ❌                 |

### Subscription lifecycle purchases

**Applicable fields:** [Common fields](#common-fields) · [Subscriber identity](#subscriber-identity-fields) · [Subscription lifecycle](#subscription-lifecycle-fields)

- **`INITIAL_PURCHASE`** — A new subscription was purchased.
- **`RENEWAL`** — An existing subscription was renewed, or a lapsed user resubscribed.
- **`CANCELLATION`** — A subscription or non-renewing purchase was canceled or refunded.
  - In the case of refunds, a subscription's auto-renewal setting may still be active. See [cancellation reasons](https://www.revenuecat.com/docs/integrations/webhooks/event-types-and-fields#cancellation-and-expiration-reasons) for more details.
  - In the case of subscription refunds, this event fires only when the latest subscription period is refunded; refunds for earlier periods do not trigger it.
- **`UNCANCELLATION`** — A non-expired canceled subscription was re-enabled.
- **`NON_RENEWING_PURCHASE`** — A customer has made a purchase that won't auto-renew.
- **`SUBSCRIPTION_PAUSED`** — The subscription was scheduled to pause at the end of the current period.
  - Don't revoke access on this event. Revoke access only on `EXPIRATION` with [expiration reason](https://www.revenuecat.com/docs/integrations/webhooks/event-types-and-fields#cancellation-and-expiration-reasons) `SUBSCRIPTION_PAUSED`.
- **`EXPIRATION`** — A subscription has expired.
  - The associated user's access should be removed.
  - With [Platform Server Notifications](https://www.revenuecat.com/docs/platform-resources/server-notifications) configured, this event occurs within seconds to minutes of expiration. Without notifications, the event might be delayed by approximately 1 hour.
- **`BILLING_ISSUE`** — An attempt to charge the subscriber failed.
  - This doesn't mean the subscription has expired.
  - You can safely ignore this event if you listen for `CANCELLATION` with `cancel_reason=BILLING_ERROR`.
- **`PRODUCT_CHANGE`** — A **subscriber** has changed the product of their subscription.
  - This doesn't mean the new subscription is in effect immediately. See [Managing Subscriptions](https://www.revenuecat.com/docs/subscription-guidance/managing-subscriptions) for more details on updates, downgrades, and crossgrades.
  - For Stripe subscriptions using Products identified by a Stripe Price ID, switching to a different Price of the same Stripe Product is also reported as a product change, because each Price maps to a distinct product in RevenueCat. See [how Stripe identifiers appear in exports](https://www.revenuecat.com/docs/integrations/scheduled-data-exports#how-do-stripe-identifiers-appear-in-exports).
- **`SUBSCRIPTION_EXTENDED`** — An existing subscription was extended.
  - The expiration date of the current subscription period was pushed back.
  - For App Store and Google Play subscriptions, this event fires when the subscription is extended through the store's API.
  - For RevenueCat Billing subscriptions, this event fires when the subscription is [extended](https://www.revenuecat.com/docs/web/web-billing/managing-customer-subscriptions#extending-subscriptions) from the dashboard or with the [extend a subscription's billing period](https://www.revenuecat.com/api-v2#tag/Subscription/operation/extend-subscription) endpoint.
  - On Google Play, this event can also fire when Google defers charging for a renewal by less than 24 hours (for unknown reasons). You may then receive `RENEWAL` or `BILLING_ISSUE` within the next 24 hours.
- **`REFUND_REVERSED`** — A refund was reversed.
- **`INVOICE_ISSUANCE`** — A new, unpaid invoice was issued.
  - A new invoice was created for a subscription or non-renewing purchase that hasn't yet been paid.
  - This only applies to RevenueCat Billing purchases, both for new checkout purchases and subscription renewals.

| Webhook Event Type      | App Store | Google Play | Amazon | Stripe | Promo | Roku | RevenueCat Billing |
| ----------------------- | --------- | ----------- | ------ | ------ | ----- | ---- | ------------------ |
| `INITIAL_PURCHASE`      | ✅        | ✅          | ✅     | ✅     | ❌    | ✅   | ✅                 |
| `RENEWAL`               | ✅        | ✅          | ✅     | ✅     | ❌    | ✅   | ✅                 |
| `CANCELLATION`          | ✅        | ✅          | ✅     | ✅     | ✅    | ✅   | ✅                 |
| `UNCANCELLATION`        | ✅        | ✅          | ✅     | ❌     | ❌    | ✅   | ✅                 |
| `NON_RENEWING_PURCHASE` | ✅        | ✅          | ✅     | ✅     | ✅    | ✅   | ✅                 |
| `SUBSCRIPTION_PAUSED`   | ❌        | ✅          | ❌     | ❌     | ❌    | ❌   | ❌                 |
| `EXPIRATION`            | ✅        | ✅          | ✅     | ✅     | ✅    | ✅   | ✅                 |
| `BILLING_ISSUE`         | ✅        | ✅          | ✅     | ✅     | ❌    | ✅   | ✅                 |
| `PRODUCT_CHANGE`        | ✅        | ✅          | ❌     | ✅     | ❌    | ❌   | ✅                 |
| `SUBSCRIPTION_EXTENDED` | ✅        | ✅          | ❌     | ✅     | ❌    | ❌   | ✅                 |
| `REFUND_REVERSED`       | ✅        | ❌          | ❌     | ❌     | ❌    | ❌   | ❌                 |
| `INVOICE_ISSUANCE`      | ❌        | ❌          | ❌     | ❌     | ❌    | ❌   | ✅                 |

### Transfer

**Applicable fields:** [Common fields](#common-fields) · [Transfer](#transfer-fields)

- **`TRANSFER`** — A transfer of transactions and entitlements was initiated between App User ID(s).
  - The webhook is sent only for the destination user, although the event appears in both customer histories. The event body is identical for both users.
  - When a transfer webhook is sent, a separate `PURCHASE_REDEEMED` webhook might also be sent for Paddle, RevenueCat Billing, or Stripe redemptions that result in a transfer.

| Webhook Event Type | App Store | Google Play | Amazon | Stripe | Promo | Roku | RevenueCat Billing |
| ------------------ | --------- | ----------- | ------ | ------ | ----- | ---- | ------------------ |
| `TRANSFER`         | ✅        | ✅          | ✅     | ✅     | ❌    | ✅   | ✅                 |

### Temporary entitlement grant

**Applicable fields:** [Common fields](#common-fields) · [Temporary entitlement grant](#temporary-entitlement-grant-fields)

- **`TEMPORARY_ENTITLEMENT_GRANT`** — RevenueCat issued a temporary outage grant to a customer.
  - RevenueCat was temporarily unable to validate a purchase with the store and granted a short-term entitlement (at most 24 hours). This prevents customers from being denied access during exceptional situations, such as partial app store outages.
  - After connectivity is restored, if validation succeeds, expect a regular `INITIAL_PURCHASE`. If validation fails, expect an `EXPIRATION` event for the temporary entitlement grant.
  - Because this event is dispatched during limited connectivity, it contains less information than a regular purchase event. In particular, it excludes subscription lifecycle or subscriber identity fields beyond `app_user_id`.

| Webhook Event Type            | App Store | Google Play | Amazon | Stripe | Promo | Roku | RevenueCat Billing |
| ----------------------------- | --------- | ----------- | ------ | ------ | ----- | ---- | ------------------ |
| `TEMPORARY_ENTITLEMENT_GRANT` | ✅        | ✅          | ✅     | ✅     | ❌    | ✅   | ❌                 |

*Interactive content is available in the web version of this doc.*

### In-app currency transaction

**Applicable fields:** [Common fields](#common-fields) · [Subscriber identity](#subscriber-identity-fields) · [In-app currency transaction](#virtual-currency-transaction-fields)

- **`VIRTUAL_CURRENCY_TRANSACTION`** — An in-app currency transaction occurred.
  - Purchases and refunds can both trigger this event.
  - For subscriptions that grant in-app currency, this event is sent for the entire subscription lifecycle whenever there is an adjustment to be made to the currency balance. This includes the initial purchase, renewals, and refunds.
  - This event provides details about the in-app currency adjustment, including the amount, currency type, and source of the change.

| Webhook Event Type             | App Store | Google Play | Amazon | Stripe | Promo | Roku | RevenueCat Billing |
| ------------------------------ | --------- | ----------- | ------ | ------ | ----- | ---- | ------------------ |
| `VIRTUAL_CURRENCY_TRANSACTION` | ✅        | ✅          | ❌     | ✅     | ❌    | ❌   | ✅                 |

### Experiment enrollment

**Applicable fields:** [Common fields](#common-fields) · [Subscriber identity](#subscriber-identity-fields) · [Experiment enrollment](#experiment-enrollment-event-fields)

- **`EXPERIMENT_ENROLLMENT`** — A customer was enrolled in an experiment.
  - This event isn't associated with a store.
  - [`app_id`](#common-fields) is usually excluded. It is included only when enrollment is linked to a dashboard store app (for example, when the SDK has reported last-seen app config data).

| Webhook Event Type      | App Store | Google Play | Amazon | Stripe | Promo | Roku | RevenueCat Billing |
| ----------------------- | --------- | ----------- | ------ | ------ | ----- | ---- | ------------------ |
| `EXPERIMENT_ENROLLMENT` | ✅        | ✅          | ✅     | ✅     | ✅    | ✅   | ✅                 |

### Purchase redemption

**Applicable fields:** [Common fields](#common-fields) · [Purchase redemption](#purchase-redemption-event-fields)

- **`PURCHASE_REDEEMED`** — A Paddle, RevenueCat Billing, or Stripe purchase was redeemed and associated with an App User ID.
  - This fires when a user opens a redemption deep link in your app.
  - If the redemption results in a transfer, this is fired **in addition** to `TRANSFER`.

| Webhook Event Type  | App Store | Google Play | Amazon | Stripe | Paddle | Promo | Roku | RevenueCat Billing |
| ------------------- | --------- | ----------- | ------ | ------ | ------ | ----- | ---- | ------------------ |
| `PURCHASE_REDEEMED` | ❌        | ❌          | ❌     | ✅     | ✅     | ❌    | ❌   | ✅                 |

### Subscriber alias

**Applicable fields:** [Common fields](#common-fields) · [Subscriber identity](#subscriber-identity-fields)

- **`SUBSCRIBER_ALIAS`** — **Deprecated.** A new App User ID was registered for an existing subscriber.
  - This is a legacy event type. New projects don't receive this webhook.

| Webhook Event Type | App Store | Google Play | Amazon | Stripe | Promo | Roku | RevenueCat Billing |
| ------------------ | --------- | ----------- | ------ | ------ | ----- | ---- | ------------------ |
| `SUBSCRIBER_ALIAS` | ✅        | ✅          | ✅     | ✅     | ✅    | ✅   | ✅                 |

### Price increase consent

**Applicable fields:** [Common fields](#common-fields) · [Subscriber identity](#subscriber-identity-fields) · [Price increase consent](#price-increase-consent-fields)

- **`PRICE_INCREASE_CONSENT_REQUIRED`** — A price increase requires customer consent before the subscription can renew at the new price.
- **`PRICE_INCREASE_CONSENT_APPROVED`** — A customer consented to a pending price increase.

| Webhook Event Type                | App Store | Google Play | Amazon | Stripe | Promo | Roku | RevenueCat Billing |
| --------------------------------- | --------- | ----------- | ------ | ------ | ----- | ---- | ------------------ |
| `PRICE_INCREASE_CONSENT_REQUIRED` | ✅        | ✅          | ❌     | ❌     | ❌    | ❌   | ❌                 |
| `PRICE_INCREASE_CONSENT_APPROVED` | ✅        | ✅          | ❌     | ❌     | ❌    | ❌   | ❌                 |

## Fields

The event fields are grouped into related attributes and referenced from the [event type groups](#event-types) above. Use the **Included** column when building parsers:

- **Always** — The JSON key is always included for the listed event types, but the value may still be `null`.
- **Sometimes** — The key is only included when data is available; it may be omitted entirely from the payload.
- **(blank)** — Nested field under a parent array or object. See the parent row for when the key is included.

Parse defensively: treat **Sometimes** fields as optional keys, and don't assume **Always** fields are non-null. Some event types omit fields listed in a group, as noted in each section.

### Common fields

**Applicable events:** All [event types](#event-types), including [Dashboard test](#dashboard-test).

These fields are on every webhook payload. `api_version` is at the root of the POST body; the rest are inside the `event` object. If HMAC signing is enabled, retries recompute the `X-RevenueCat-Webhook-Signature` header. See [Signature timestamp on retries](https://www.revenuecat.com/docs/integrations/webhooks#signature-timestamp-on-retries).

| Field                | Type    | Description                                                                                                                                                                                 | Included                                                                                                                       |
| -------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `api_version`        | String  | API version of the webhook format.                                                                                                                                                          | Always                                                                                                                         |
| `type`               | String  | [Type of the event](#event-types).                                                                                                                                                          | Always                                                                                                                         |
| `id`                 | String  | Unique identifier of the event. **Retries reuse the same `id` and `event_timestamp_ms`.**                                                                                                   | Always                                                                                                                         |
| `event_timestamp_ms` | Integer | The time that the event was generated, which doesn't necessarily coincide with when the action that triggered the event occurred. **Retries reuse the same `id` and `event_timestamp_ms`.** | Always                                                                                                                         |
| `app_id`             | String  | Public identifier of the dashboard app (store configuration) associated with the event. Found in project settings.                                                                          | Always, except when `store` is `PROMOTIONAL` or when the event has no linked app configuration (e.g. `EXPERIMENT_ENROLLMENT`). |

### Subscriber identity fields

**Applicable events:** [Dashboard test](#dashboard-test) · [Subscription lifecycle purchases](#subscription-lifecycle-purchases) · [In-app currency transaction](#virtual-currency-transaction) [**†**](#subscriber-identity-vct-exception) · [Experiment enrollment](#experiment-enrollment) · [Subscriber alias](#subscriber-alias) · [Price increase consent](#price-increase-consent).

:::info
When looking up users from the webhook in your systems, make sure to search both the `original_app_user_id` and the `aliases` array.
:::

| Field                              | Type    | Description                                                                                                       | Included [**†**](#subscriber-identity-vct-exception) |
| ---------------------------------- | ------- | ----------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| `app_user_id`                      | String  | Last seen App User ID of the subscriber.                                                                          | Always                                               |
| `original_app_user_id`             | String  | The first App User ID used by the subscriber.                                                                     | Always                                               |
| `aliases`                          | Array   | All App User IDs ever used by the subscriber.                                                                     | Always                                               |
| `subscriber_attributes`            | Map     | Map of attribute names to attribute objects. See the [customer attributes guide](https://www.revenuecat.com/docs/customers/customer-attributes). | Sometimes                                            |
| `experiments`                      | Array   | Experiments the subscriber was enrolled in at event time. Each entry has the attributes listed below.             | Sometimes                                            |
| `experiments[].experiment_id`      | String  | ID of the experiment.                                                                                             |                                                      |
| `experiments[].experiment_variant` | String  | Variant the subscriber was enrolled in.                                                                           |                                                      |
| `experiments[].enrolled_at_ms`     | Integer | When the subscriber was enrolled, in milliseconds since Unix epoch. This can be `null`.                           |                                                      |

*Interactive content is available in the web version of this doc.*

:::note[Exception: `VIRTUAL_CURRENCY_TRANSACTION`]
**†** `VIRTUAL_CURRENCY_TRANSACTION` doesn't use this field group as written above. When data is available, `app_user_id`, `aliases`, and `subscriber_attributes` might be included (**Sometimes**). `original_app_user_id` and `experiments` aren't included. See [In-app currency transaction fields](#virtual-currency-transaction-fields).
:::

### Subscription lifecycle fields

**Applicable events:** [Dashboard test](#dashboard-test) · [Subscription lifecycle purchases](#subscription-lifecycle-purchases).

| Field                           | Type    | Description                                                                                                                                                                                                                                                                  | Included  |
| ------------------------------- | ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| `product_id`                    | String  | Product identifier of the subscription. For Google Play products set up in RevenueCat after February 2023, this identifier has the format `<subscription_id>:<base_plan_id>`.                                                                                                | Always    |
| `period_type`                   | String  | Period type of the transaction: `TRIAL`, `INTRO`, `NORMAL`, `PROMOTIONAL`, or `PREPAID`.                                                                                                                                                                                     | Always    |
| `purchased_at_ms`               | Integer | Time when the transaction was purchased, in milliseconds since Unix epoch.                                                                                                                                                                                                   | Always    |
| `expiration_at_ms`              | Integer | Expiration of the transaction, in milliseconds since Unix epoch. This can be `null` for non-subscription purchases or lifetime products.                                                                                                                                     | Always    |
| `environment`                   | String  | Store environment: `SANDBOX` or `PRODUCTION`.                                                                                                                                                                                                                                | Always    |
| `entitlement_id`                | String  | **Deprecated.** See `entitlement_ids`.                                                                                                                                                                                                                                       | Always    |
| `entitlement_ids`               | Array   | Entitlement identifiers of the subscription. This can be `null` if the `product_id` is not mapped to any entitlements.                                                                                                                                                       | Always    |
| `presented_offering_id`         | String  | Offering presented during the initial purchase. This can be `null`. Not available for apps using legacy entitlements.                                                                                                                                                        | Always    |
| `transaction_id`                | String  | Transaction identifier from the store.                                                                                                                                                                                                                                       | Always    |
| `original_transaction_id`       | String  | `transaction_id` of the original transaction in the subscription.                                                                                                                                                                                                            | Always    |
| `is_family_share`               | Boolean | Whether the purchase was shared via [Family Sharing](https://help.apple.com/app-store-connect/#/dev45b03fab9). This is always `false` for purchases made outside the App Store.                                                                                              | Always    |
| `country_code`                  | String  | [ISO 3166-1 Alpha-2](https://www.iban.com/country-codes) country code derived from the subscriber's last seen location: `US`, `CA`, etc.                                                                                                                                     | Always    |
| `store`                         | String  | Store the purchase belongs to: `AMAZON`, `APP_STORE`, `MAC_APP_STORE`, `PADDLE`, `PLAY_STORE`, `PROMOTIONAL`, `RC_BILLING`, `ROKU`, `STRIPE`, `TEST_STORE`                                                                                                                   | Sometimes |
| `currency`                      | String  | [ISO 4217](https://www.iban.com/currency-codes) currency code. Can be `null` if unknown: `USD`, `CAD`, etc.                                                                                                                                                                  | Sometimes |
| `price`                         | Double  | USD price of the transaction. This can be `null` if unknown, `0` for free trials, or negative for refunds.                                                                                                                                                                   | Sometimes |
| `price_in_purchased_currency`   | Double  | Price in the purchase currency. This can be `null` if unknown, `0` for free trials, or negative for refunds.                                                                                                                                                                 | Sometimes |
| `tax_percentage`                | Double  | Estimated tax percentage deducted from the transaction. This can be `null` if unknown.                                                                                                                                                                                       | Sometimes |
| `commission_percentage`         | Double  | Estimated store commission percentage. This can be `null` if unknown.                                                                                                                                                                                                        | Sometimes |
| `takehome_percentage`           | Double  | **Deprecated.** Use `tax_percentage` and `commission_percentage` instead. [Learn more](https://www.revenuecat.com/docs/dashboard-and-metrics/taxes-and-commissions).                                                                                                                                        | Sometimes |
| `offer_code`                    | String  | Offer or promotion code used for the transaction. This can be `null`. Available for App Store and Google Play.                                                                                                                                                               | Sometimes |
| `renewal_number`                | Integer | Number of renewals this subscription has gone through. Starts at `1`; trial conversions count as renewals.                                                                                                                                                                   | Sometimes |
| `metadata`                      | Object  | Developer-defined [metadata](https://www.revenuecat.com/docs/web/custom-metadata) attached to RevenueCat Billing transaction.                                                                                                                                                                               | Sometimes |
| `discount_percentage`           | Double  | Discount percentage applied to the transaction.                                                                                                                                                                                                                              | Sometimes |
| `discount_amount`               | Double  | Discount amount applied to the transaction.                                                                                                                                                                                                                                  | Sometimes |
| `discount_identifier`           | String  | Identifier of the discount applied to the transaction.                                                                                                                                                                                                                       | Sometimes |
| `quantity`                      | Integer | Quantity purchased. This is only included in `NON_RENEWING_PURCHASE` for some projects.                                                                                                                                                                                      | Sometimes |
| `grace_period_expiration_at_ms` | Integer | Grace period expiration. This is only included on `BILLING_ISSUE` and always present on that event type. This can be `null`.                                                                                                                                                 | Sometimes |
| `auto_resume_at_ms`             | Integer | When a paused Google Play subscription resumes. This is only included on `SUBSCRIPTION_PAUSED` and always present on that event type.                                                                                                                                        | Sometimes |
| `is_trial_conversion`           | Boolean | Whether the previous period was a free trial. This is only included on `RENEWAL`.                                                                                                                                                                                            | Sometimes |
| `cancel_reason`                 | String  | Reason for `CANCELLATION`. This isn't included on other events. See [Cancellation and Expiration Reasons](#cancellation-and-expiration-reasons).                                                                                                                             | Sometimes |
| `expiration_reason`             | String  | Reason for `EXPIRATION`. This isn't included on other events. See [Cancellation and Expiration Reasons](#cancellation-and-expiration-reasons): `UNSUBSCRIBE`, `BILLING_ERROR`, `DEVELOPER_INITIATED`, `PRICE_INCREASE`, `CUSTOMER_SUPPORT`, `UNKNOWN`, `SUBSCRIPTION_PAUSED` | Sometimes |
| `new_product_id`                | String  | Product the subscriber switched to. This is only included on `PRODUCT_CHANGE` for App Store, deferred Google Play changes, and RevenueCat Billing (where `product_id` is the product the subscriber switched from). This is omitted from the payload when `null`.            | Sometimes |

`discount_percentage`, `discount_amount`, and `discount_identifier` apply only to `INITIAL_PURCHASE`, `RENEWAL`, and `NON_RENEWING_PURCHASE` within [Subscription lifecycle purchases](#subscription-lifecycle-purchases).

:::info[Determine trial and subscription duration]
Subtract `purchased_at_ms` from `expiration_at_ms` to get the period duration in milliseconds.
:::

:::info[App Store renewal timestamps]
For App Store renewals, Apple may charge up to 24 hours before the billing period starts, so `purchased_at_ms` (billing period start) can be later than `event_timestamp_ms` (when payment was collected).
:::

### Transfer fields

**Applicable events:** [Transfer](#transfer).

| Field                   | Type     | Description                                                  | Included  |
| ----------------------- | -------- | ------------------------------------------------------------ | --------- |
| `transferred_from`      | String[] | App User ID(s) transactions and entitlements are taken from. | Always    |
| `transferred_to`        | String[] | App User ID(s) receiving the transactions and entitlements.  | Always    |
| `subscriber_attributes` | Map      | Subscriber attributes for the destination subscriber.        | Sometimes |
| `store`                 | String   | Store of the transferred purchases.                          | Sometimes |
| `environment`           | String   | Store environment.                                           | Sometimes |

### Temporary entitlement grant fields

**Applicable events:** [Temporary entitlement grant](#temporary-entitlement-grant).

| Field         | Type   | Description                                                     | Included  |
| ------------- | ------ | --------------------------------------------------------------- | --------- |
| `app_user_id` | String | App User ID of the subscriber who received the temporary grant. | Always    |
| `store`       | String | Store of the purchase.                                          | Sometimes |

After the outage resolves, expect a regular `INITIAL_PURCHASE` or `EXPIRATION` event for the temporary entitlement grant.

*Interactive content is available in the web version of this doc.*

### In-app currency transaction fields

**Applicable events:** [In-app currency transaction](#virtual-currency-transaction).

| Field                                | Type    | Description                                                                                                                                              | Included  |
| ------------------------------------ | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| `source`                             | String  | Source of the in-app currency adjustment: `in_app_purchase`, `admin_api`, `server_api`, `client_sdk`, `expiration`, `ad_reward`                          | Sometimes |
| `virtual_currency_transaction_id`    | String  | Unique identifier for this in-app currency transaction.                                                                                                  | Sometimes |
| `adjustments`                        | Array   | In-app currency balance changes in this transaction. Each entry has the attributes listed below.                                                         | Sometimes |
| `adjustments[].amount`               | Integer | Amount added (positive) or removed (negative) from the customer's balance for this currency.                                                             |           |
| `adjustments[].currency`             | Object  | Details about the in-app currency involved in the adjustment.                                                                                            |           |
| `adjustments[].currency.code`        | String  | Unique identifier for the in-app currency.                                                                                                               |           |
| `adjustments[].currency.name`        | String  | Display name of the in-app currency.                                                                                                                     |           |
| `adjustments[].currency.description` | String  | Description of the in-app currency.                                                                                                                      |           |
| `product_id`                         | String  | In-app purchase product identifier. Populated when `source` is `in_app_purchase`.                                                                        | Sometimes |
| `product_display_name`               | String  | Display name of the product. Populated when `source` is `in_app_purchase`.                                                                               | Sometimes |
| `transaction_id`                     | String  | In-app purchase transaction identifier. Populated when `source` is `in_app_purchase`.                                                                    | Sometimes |
| `store`                              | String  | Store of the in-app purchase. Populated when `source` is `in_app_purchase`: `AMAZON`, `APP_STORE`, `MAC_APP_STORE`, `PLAY_STORE`, `RC_BILLING`, `STRIPE` | Sometimes |
| `purchase_environment`               | String  | Environment of the in-app purchase: `SANDBOX`, `PRODUCTION`. Populated when `source` is `in_app_purchase`.                                               | Sometimes |
| `updated_balance`                    | Integer | Customer balance after the adjustment.                                                                                                                   | Sometimes |
| `ad_transaction_id`                  | String  | Client transaction ID for ad reward verification. Populated only when `source` is `ad_reward`.                                                           | Sometimes |

### Experiment enrollment event fields

**Applicable events:** [Experiment enrollment](#experiment-enrollment).

:::info
This event type usually has no linked dashboard store app, so [`app_id`](#common-fields) is typically omitted from the payload. When enrollment is tied to a store app configuration, `app_id` is included with the public identifier of that app.
:::

| Field                       | Type    | Description                                                                               | Included  |
| --------------------------- | ------- | ----------------------------------------------------------------------------------------- | --------- |
| `experiment_id`             | String  | ID of the experiment the customer was enrolled in.                                        | Always    |
| `experiment_variant`        | String  | Variant the customer was enrolled in.                                                     | Always    |
| `experiment_enrolled_at_ms` | Integer | When the customer was enrolled, in milliseconds since Unix epoch.                         | Always    |
| `offering_id`               | String  | Offering ID of the enrolled variant.                                                      | Sometimes |
| `country_code`              | String  | [ISO 3166-1 Alpha-2](https://www.iban.com/country-codes) country code for the enrollment. | Sometimes |

### Purchase redemption event fields

**Applicable events:** [Purchase redemption](#purchase-redemption).

When `redemption_outcome` is `transfer`, a `TRANSFER` webhook is also sent for the destination user.

| Field                 | Type     | Description                                                                                                                                          | Included  |
| --------------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| `redeemed_from`       | String[] | App User ID(s) of the original web purchaser.                                                                                                        | Sometimes |
| `redeemed_by`         | String[] | App User ID(s) of the redeemer.                                                                                                                      | Sometimes |
| `redemption_outcome`  | String   | How the redemption was resolved: `alias`, `transfer`, `redeemer_owns`                                                                                | Sometimes |
| `redemption_platform` | String   | Platform of the redeem request (from the SDK `X-Platform` header): `ios`, `android`, `macos`, `amazon`, `web`, etc. (lowercase). This can be `null`. | Sometimes |
| `store`               | String   | Store of the redeemed purchase: `PADDLE`, `RC_BILLING`, `STRIPE`                                                                                     | Sometimes |
| `environment`         | String   | Environment of the redeemed purchase: `SANDBOX`, `PRODUCTION`                                                                                        | Sometimes |
| `product_id`          | String   | Product identifier of the redeemed purchase. This can be `null`.                                                                                     | Sometimes |
| `entitlement_ids`     | Array    | Entitlements associated with the redeemed purchase. This can be `null`.                                                                              | Sometimes |
| `workflow_id`         | String   | Funnel workflow ID, if the purchase originated from a Funnel.                                                                                        | Sometimes |
| `workflow_step_id`    | String   | Funnel step ID that triggered checkout.                                                                                                              | Sometimes |
| `trace_id`            | String   | Trace ID linking the redemption to the original checkout session.                                                                                    | Sometimes |

`redemption_outcome` values:

- `alias`: The redeeming App User ID was aliased to the original web purchaser.
- `transfer`: The purchase was transferred to the redeemer. A `TRANSFER` event is also sent.
- `redeemer_owns`: The redeemer already owned the purchase. No alias or transfer occurred.

:::info
`PURCHASE_REDEEMED` only fires for successful redemptions. Expired or invalid tokens do not generate a webhook.
:::

### Price increase consent fields

**Applicable events:** [Price increase consent](#price-increase-consent).

Uses [Subscriber identity fields](#subscriber-identity-fields). Does not use the full subscription lifecycle field group.

| Field                     | Type   | Description                                                                     | Included  |
| ------------------------- | ------ | ------------------------------------------------------------------------------- | --------- |
| `product_id`              | String | Product identifier of the subscription.                                         | Always    |
| `transaction_id`          | String | Transaction identifier.                                                         | Always    |
| `original_transaction_id` | String | Original subscription transaction identifier.                                   | Always    |
| `store`                   | String | Store of the subscription.                                                      | Sometimes |
| `environment`             | String | Store environment.                                                              | Sometimes |
| `currency`                | String | [ISO 4217](https://www.iban.com/currency-codes) currency code of the new price. | Sometimes |
| `country_code`            | String | [ISO 3166-1 Alpha-2](https://www.iban.com/country-codes) country code.          | Sometimes |
| `subscriber_attributes`   | Map    | Subscriber attributes.                                                          | Sometimes |

## Cancellation and Expiration Reasons

| Reason                | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | App Store | Google Play | Amazon | Web | Promo |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- | ----------- | ------ | --- | ----- |
| `UNSUBSCRIBE`         | Subscriber canceled voluntarily. **This event fires when a user unsubscribes, not when the subscription expires.**                                                                                                                                                                                                                                                                                                                                                                                  | ✅        | ✅          | ✅     | ✅  | ❌    |
| `BILLING_ERROR`       | Apple, Amazon, or Google could not charge the subscriber using their payment method. The `CANCELLATION` event with cancellation reason `BILLING_ERROR` is fired as soon as the billing issue has been detected. The `EXPIRATION` event with expiration reason `BILLING_ERROR` is fired if the grace period (if set up) has ended without recovering the payment, and the customer should lose access to the subscription.                                                                           | ✅        | ✅          | ✅     | ❌  | ❌    |
| `DEVELOPER_INITIATED` | Developer canceled the subscription.                                                                                                                                                                                                                                                                                                                                                                                                                                                                | ✅        | ✅          | ❌     | ❌  | ✅    |
| `PRICE_INCREASE`      | Subscriber did not agree to a price increase.                                                                                                                                                                                                                                                                                                                                                                                                                                                       | ✅        | ✅          | ❌     | ❌  | ❌    |
| `CUSTOMER_SUPPORT`    | Customer received a refund from Apple support, a Google Play subscription was refunded through RevenueCat, an Amazon subscription was refunded through Amazon support, or a web (RevenueCat Billing or Stripe Billing) subscription was refunded. Note that this doesn't mean that a subscription's autorenewal preference has been deactivated since refunds can be given without canceling a subscription. Check the current subscription status to see whether the subscription is still active. | ✅        | ✅          | ✅     | ✅  | ❌    |
| `UNKNOWN`             | Apple did not provide the reason for the cancellation.                                                                                                                                                                                                                                                                                                                                                                                                                                              | ✅        | ❌          | ❌     | ❌  | ❌    |
| `SUBSCRIPTION_PAUSED` | The subscription expired because it was paused (only `EXPIRATION` event).                                                                                                                                                                                                                                                                                                                                                                                                                           | ❌        | ✅          | ❌     | ❌  | ❌    |
