---
id: "integrations/webhooks/sample-events"
title: "Sample Events"
description: "These are some representative samples of webhooks you might receive from RevenueCat. Keep in mind that webhooks can include additional fields to what's shown here."
permalink: "/docs/integrations/webhooks/sample-events"
slug: "sample-events"
version: "current"
original_source: "docs/integrations/webhooks/sample-events.mdx"
---

> **AI agents:** This is the Markdown version of a RevenueCat documentation page. For the complete documentation index, see [llms.txt](https://www.revenuecat.com/docs/llms.txt).

These are some representative samples of webhooks you might receive from RevenueCat. Keep in mind that webhooks can include additional fields to what's shown here.

**Initial Purchase**

```json
{
    "event": {
        "event_timestamp_ms": 1658726378679,
        "product_id": "com.subscription.weekly",
        "period_type": "NORMAL",
        "purchased_at_ms": 1658726374000,
        "expiration_at_ms": 1659331174000,
        "environment": "PRODUCTION",
        "entitlement_id": null,
        "entitlement_ids": [
            "pro"
        ],
        "presented_offering_id": null,
        "transaction_id": "123456789012345",
        "original_transaction_id": "123456789012345",
        "is_family_share": false,
        "country_code": "US",
        "app_user_id": "1234567890",
        "aliases": [
            "$RCAnonymousID:8069238d6049ce87cc529853916d624c"
        ],
        "original_app_user_id": "$RCAnonymousID:87c6049c58069238dce29853916d624c",
        "currency": "USD",
        "price": 4.99,
        "price_in_purchased_currency": 4.99,
        "subscriber_attributes": {
            "$email": {
                "updated_at_ms": 1662955084635,
                "value": "firstlast@gmail.com"
            }
        },
        "store": "APP_STORE",
        "takehome_percentage": 0.7,
        "tax_percentage": 0.0,
        "commission_percentage": 0.3,
        "offer_code": null,
        "type": "INITIAL_PURCHASE",
        "id": "12345678-1234-1234-1234-123456789012",
        "app_id": "1234567890",
        "experiments": [
            {
                "experiment_id": "prexp123",
                "experiment_variant": "b",
                "enrolled_at_ms": 1658726378679
            }
        ]
    },
    "api_version": "1.0"
}
```

**Renewal**

```json
{
    "event": {
        "event_timestamp_ms": 1658726405017,
        "product_id": "com.subscription.weekly",
        "period_type": "NORMAL",
        "purchased_at_ms": 1658755132000,
        "expiration_at_ms": 1659359932000,
        "environment": "PRODUCTION",
        "entitlement_id": null,
        "entitlement_ids": [
            "pro"
        ],
        "presented_offering_id": null,
        "transaction_id": "123456789012345",
        "original_transaction_id": "123456789012345",
        "is_family_share": false,
        "country_code": "DE",
        "app_user_id": "1234567890",
        "aliases": [
            "$RCAnonymousID:8069238d6049ce87cc529853916d624c"
        ],
        "original_app_user_id": "$RCAnonymousID:87c6049c58069238dce29853916d624c",
        "currency": "EUR",
        "is_trial_conversion": false,
        "price": 8.14,
        "price_in_purchased_currency": 7.99,
        "subscriber_attributes": {
            "$email": {
                "updated_at_ms": 1662955084635,
                "value": "firstlast@gmail.com"
            }
        },
        "store": "APP_STORE",
        "takehome_percentage": 0.7,
        "tax_percentage": 0.0,
        "commission_percentage": 0.3,
        "offer_code": null,
        "type": "RENEWAL",
        "id": "12345678-1234-1234-1234-123456789012",
        "app_id": "1234567890",
        "experiments": [
            {
                "experiment_id": "prexp123",
                "experiment_variant": "b",
                "enrolled_at_ms": 1762274791000
            }
        ]
    },
    "api_version": "1.0"
}
```

**Cancellation**

```json
{
  "event": {
    "event_timestamp_ms": 1601337615995,
    "product_id": "com.revenuecat.myapp.weekly",
    "period_type": "NORMAL",
    "purchased_at_ms": 1601417766000,
    "expiration_at_ms": 1602022566000,
    "environment": "PRODUCTION",
    "entitlement_id": "pro",
    "entitlement_ids": [
      "pro"
    ],
    "presented_offering_id": "defaultoffering",
    "transaction_id": "100000000000002",
    "original_transaction_id": "100000000000000",
    "is_family_share": false,
    "country_code": "US",
    "app_user_id": "$RCAnonymousID:12345678-1234-1234-1234-123456789123",
    "aliases": [
      "$RCAnonymousID:12345678-1234-ABCD-1234-123456789123",
      "user_1234"
    ],
    "offer_code": "free_month",
    "original_app_user_id": "$RCAnonymousID:12345678-1234-ABCD-1234-123456789123",
    "cancel_reason": "UNSUBSCRIBE",
    "currency": "USD",
    "price": 0.0,
    "price_in_purchased_currency": 0.0,
    "subscriber_attributes": {
      "$idfa": {
        "value": "12345678-1234-1234-1234-12345678912x",
        "updated_at_ms": 1578018408238
      },
      "$appsflyerId": {
        "value": "1234567891234-1234567",
        "updated_at_ms": 1578018408238
      },
      "favorite_food": {
        "value": "pizza",
        "updated_at_ms": 1578018408238
      }
    },
    "store": "APP_STORE",
    "takehome_percentage": 0.7,
    "tax_percentage": 0.0,
    "commission_percentage": 0.3,
    "type": "CANCELLATION",
    "id": "12345678-ABCD-1234-ABCD-12345678912",
    "app_id": "1234567890",
    "experiments": [
      {
        "experiment_id": "prexp123",
        "experiment_variant": "b",
        "enrolled_at_ms": 1762274791000
      }
    ]
  },
  "api_version": "1.0"
}
```

**Uncancellation**

```json
{
    "event": {
        "event_timestamp_ms": 1663982135337,
        "product_id": "com.subscription.monthly",
        "period_type": "NORMAL",
        "purchased_at_ms": 1662643092000,
        "expiration_at_ms": 1665235092000,
        "environment": "PRODUCTION",
        "entitlement_id": null,
        "entitlement_ids": [
            "plus"
        ],
        "presented_offering_id": "plus",
        "transaction_id": "123456789012345",
        "original_transaction_id": "123456789012345",
        "is_family_share": false,
        "country_code": "US",
        "app_user_id": "1234567890",
        "aliases": [
            "$RCAnonymousID:8069238d6049ce87cc529853916d624c"
        ],
        "original_app_user_id": "$RCAnonymousID:87c6049c58069238dce29853916d624c",
        "currency": "USD",
        "price": 0.0,
        "price_in_purchased_currency": 0.0,
        "subscriber_attributes": {},
        "store": "APP_STORE",
        "takehome_percentage": 0.7,
        "offer_code": null,
        "tax_percentage": 0.0,
        "commission_percentage": 0.3,
        "type": "UNCANCELLATION",
        "id": "12345678-1234-1234-1234-123456789012",
        "app_id": "1234567890",
        "experiments": [
            {
                "experiment_id": "prexp123",
                "experiment_variant": "b",
                "enrolled_at_ms": 1762274791000
            }
        ]
    },
    "api_version": "1.0"
}
```

**Non Renewing Purchase**

```json
{
    "event": {
        "event_timestamp_ms": 1658726522314,
        "product_id": "2100_tokens",
        "period_type": "NORMAL",
        "purchased_at_ms": 1658726519000,
        "expiration_at_ms": null,
        "environment": "PRODUCTION",
        "entitlement_id": null,
        "entitlement_ids": [
            "pro"
        ],
        "presented_offering_id": "coins",
        "transaction_id": "123456789012345",
        "original_transaction_id": "123456789012345",
        "is_family_share": false,
        "country_code": "CA",
        "app_user_id": "1234567890",
        "aliases": [
            "$RCAnonymousID:8069238d6049ce87cc529853916d624c"
        ],
        "original_app_user_id": "$RCAnonymousID:87c6049c58069238dce29853916d624c",
        "currency": "CAD",
        "price": 25.487,
        "price_in_purchased_currency": 32.99,
        "subscriber_attributes": {
            "$email": {
                "updated_at_ms": 1662955084635,
                "value": "firstlast@gmail.com"
            }
        },
        "store": "APP_STORE",
        "takehome_percentage": 0.85,
        "tax_percentage": 0.0,
        "commission_percentage": 0.15,
        "offer_code": null,
        "type": "NON_RENEWING_PURCHASE",
        "id": "12345678-1234-1234-1234-123456789012",
        "app_id": "1234567890",
        "experiments": [
            {
                "experiment_id": "prexp123",
                "experiment_variant": "b",
                "enrolled_at_ms": 1762274791000
            }
        ]
    },
    "api_version": "1.0"
}
```

**Subscription Paused**

```json
{
    "event": {
        "event_timestamp_ms": 1652796516000,
        "product_id": "premium",
        "period_type": "NORMAL",
        "purchased_at_ms": 1652681048845,
        "expiration_at_ms": 1655366648845,
        "environment": "PRODUCTION",
        "entitlement_id": "Premium1",
        "entitlement_ids": [
            "Premium1"
        ],
        "presented_offering_id": "premium",
        "transaction_id": "123456789012345",
        "original_transaction_id": "123456789012345",
        "is_family_share": false,
        "country_code": "US",
        "app_user_id": "1234567890",
        "aliases": [
            "$RCAnonymousID:8069238d6049ce87cc529853916d624c"
        ],
        "original_app_user_id": "$RCAnonymousID:87c6049c58069238dce29853916d624c",
        "auto_resume_at_ms": 1657951448845,
        "currency": "USD",
        "price": 0.0,
        "price_in_purchased_currency": 0.0,
        "subscriber_attributes": {
            "$email": {
                "updated_at_ms": 1662955084635,
                "value": "firstlast@gmail.com"
            }
        },
        "store": "PLAY_STORE",
        "takehome_percentage": 0.85,
        "tax_percentage": 0.0,
        "commission_percentage": 0.15,
        "offer_code": null,
        "type": "SUBSCRIPTION_PAUSED",
        "id": "12345678-1234-1234-1234-123456789012",
        "app_id": "1234567890",
        "experiments": [
            {
                "experiment_id": "prexp123",
                "experiment_variant": "b",
                "enrolled_at_ms": 1762274791000
            }
        ]
    },
    "api_version": "1.0"
}
```

**Billing Issue**

```json
{
  "event" : {
    "event_timestamp_ms" : 1601337601013,
    "product_id" : "com.revenuecat.myapp.monthly",
    "period_type" : "NORMAL",
    "purchased_at_ms" : 1598640647000,
    "expiration_at_ms" : 1601319047000,
    "grace_period_expiration_at_ms" : 1601933447000,
    "environment" : "PRODUCTION",
    "entitlement_id" : "pro",
    "entitlement_ids" : [
      "pro"
    ],
    "presented_offering_id" : "defaultoffering",
    "transaction_id" : "100000000000002",
    "original_transaction_id" : "100000000000000",
    "is_family_share" : false,
    "country_code" : "US",
    "app_user_id" : "$RCAnonymousID:12345678-1234-1234-1234-123456789123",
    "aliases" : [
      "$RCAnonymousID:12345678-1234-1234-1234-123456789123"
    ],
    "offer_code": "summer_special",
    "original_app_user_id" : "$RCAnonymousID:12345678-1234-1234-1234-123456789123",
    "currency" : "USD",
    "price" : 0,
    "price_in_purchased_currency" : 0,
    "subscriber_attributes" : {
      "$idfa" : {
        "value" : "12345678-1234-1234-1234-12345678912x",
        "updated_at_ms" : 1578018408238
      },
      "$appsflyerId" : {
        "value" : "1234567891234-1234567",
        "updated_at_ms" : 1578018408238
      }
    },
    "store" : "APP_STORE",
    "takehome_percentage" : 0.7,
    "tax_percentage": 0.0,
    "commission_percentage": 0.3,
    "type" : "BILLING_ISSUE",
    "id" : "12345678-1234-1234-1234-12345678912",
    "app_id" : "1234567890",
    "experiments": [
      {
        "experiment_id": "prexp123",
        "experiment_variant": "b",
        "enrolled_at_ms": 1762274791000
      }
    ]
  },
  "api_version" : "1.0"
}
```

**Expiration**

```json
{
  "event": {
    "event_timestamp_ms": 1697451462232,
    "product_id": "com.subscription.weekly",
    "period_type": "NORMAL",
    "purchased_at_ms": 1696846623000,
    "expiration_at_ms": 1697451423000,
    "environment": "PRODUCTION",
    "entitlement_id": null,
    "entitlement_ids": ["pro"],
    "presented_offering_id": null,
    "transaction_id": "123456789012345",
    "original_transaction_id": "123456789012345",
    "is_family_share": false,
    "country_code": "US",
    "app_user_id": "1234567890",
    "aliases": ["$RCAnonymousID:8069238d6049ce87cc529853916d624c"],
    "original_app_user_id": "$RCAnonymousID:8069238d6049ce87cc529853916d624c",
    "expiration_reason": "UNSUBSCRIBE",
    "currency": "USD",
    "price": 0.00,
    "price_in_purchased_currency": 0.00,
    "subscriber_attributes": {
        "$email": {
            "updated_at_ms": 1662955084635,
            "value": "firstlast@gmail.com"
        }
    },
    "store": "APP_STORE",
    "takehome_percentage": 0.7,
    "offer_code": null,
    "tax_percentage": 0.012,
    "commission_percentage": 0.3,
    "type": "EXPIRATION",
    "id": "12345678-1234-1234-1234-123456789012",
    "app_id": "1234567890",
    "experiments": [
      {
        "experiment_id": "prexp123",
        "experiment_variant": "b",
        "enrolled_at_ms": 1762274791000
      }
    ]
  },
  "api_version": "1.0"
}
```

**Transfer**

```json
{
 "event": {
     "app_id": "1234567890",
     "event_timestamp_ms": 78789789798798,
     "id": "CD489E0E-5D52-4E03-966B-A7F17788E432",
     "store": "APP_STORE",
     "transferred_from": ["00005A1C-6091-4F81-BE77-F0A83A271AB6"],
     "transferred_to": ["4BEDB450-8EF2-11E9-B475-0800200C9A66"],
     "type": "TRANSFER",
     "environment": "PRODUCTION"
 },
 "api_version": "1.0"
}
```

**Refund**

```json
{
  "event" : {
    "event_timestamp_ms" : 1601337615995,
    "product_id" : "com.revenuecat.myapp.monthly",
    "period_type" : "NORMAL",
    "purchased_at_ms" : 1601258901000,
    "expiration_at_ms" : 1601336705000,
    "environment" : "PRODUCTION",
    "entitlement_id" : "pro",
    "entitlement_ids" : [
      "pro"
    ],
    "presented_offering_id" : "defaultoffering",
    "transaction_id" : "100000000000000",
    "original_transaction_id" : "100000000000000",
    "is_family_share" : false,
    "country_code" : "US",
    "app_user_id" : "$RCAnonymousID:12345678-1234-ABCD-1234-123456789123",
    "aliases" : [
      "$RCAnonymousID:12345678-1234-1234-1234-123456789123",
      "user_1234"
    ],
    "offer_code": null,
    "original_app_user_id" : "$RCAnonymousID:12345678-1234-1234-1234-123456789123",
    "cancel_reason" : "CUSTOMER_SUPPORT",
    "currency" : "USD",
    "price" : -9.99,
    "price_in_purchased_currency" : -9.99,
    "subscriber_attributes" : {
      "$idfa" : {
        "value" : "12345678-1234-1234-1234-12345678912x",
        "updated_at_ms" : 1578018408238
      },
      "$appsflyerId" : {
        "value" : "1234567891234-1234567",
        "updated_at_ms" : 1578018408238
      }
    },
    "store" : "APP_STORE",
    "takehome_percentage" : 0.7,
    "tax_percentage": 0.1109,
    "commission_percentage": 0.3,
    "type" : "CANCELLATION",
    "id" : "12345678-1234-1234-1234-12345678912",
    "app_id" : "1234567890",
    "experiments": [
      {
        "experiment_id": "prexp123",
        "experiment_variant": "b",
        "enrolled_at_ms": 1762274791000
      }
    ]
  },
  "api_version" : "1.0"
}
```

**Refund Reversed**

```json
{
  "event": {
    "event_timestamp_ms": 1697451462232,
    "product_id": "com.subscription.weekly",
    "period_type": "NORMAL",
    "purchased_at_ms": 1696846623000,
    "expiration_at_ms": 1697451423000,
    "environment": "PRODUCTION",
    "entitlement_id": null,
    "entitlement_ids": ["pro"],
    "presented_offering_id": null,
    "transaction_id": "123456789012345",
    "original_transaction_id": "123456789012345",
    "is_family_share": false,
    "country_code": "US",
    "app_user_id": "1234567890",
    "aliases": ["$RCAnonymousID:8069238d6049ce87cc529853916d624c"],
    "original_app_user_id": "1234567890",
    "currency": "USD",
    "price": 5.00,
    "price_in_purchased_currency": 5.00,
    "subscriber_attributes": {
        "$email": {
            "updated_at_ms": 1662955084635,
            "value": "firstlast@gmail.com"
        }
    },
    "store": "APP_STORE",
    "takehome_percentage": 0.7,
    "offer_code": null,
    "tax_percentage": 0.012,
    "commission_percentage": 0.3,
    "metadata": null,
    "renewal_number": 3,
    "type": "REFUND_REVERSED",
    "id": "12345678-1234-1234-1234-123456789012",
    "app_id": "1234567890",
    "experiments": [
      {
        "experiment_id": "prexp123",
        "experiment_variant": "b",
        "enrolled_at_ms": 1762274791000
      }
    ]
  },
  "api_version": "1.0"
}
```

**Product Change**

```json
{
  "event" : {
    "event_timestamp_ms" : 1601338594769,
    "product_id" : "com.revenuecat.myapp.monthly",
    "period_type" : "NORMAL",
    "purchased_at_ms" : 1601304429682,
    "expiration_at_ms" : 1601311606660,
    "environment" : "PRODUCTION",
    "entitlement_id" : "subscription",
    "entitlement_ids" : [
      "subscription"
    ],
    "presented_offering_id" : "defaultoffering",
    "transaction_id" : "GPA.1234-1234-1234-12345",
    "original_transaction_id" : "GPA.1234-1234-1234-12345",
    "is_family_share" : false,
    "country_code" : "US",
    "app_user_id" : "$RCAnonymousID:12345678-1234-1234-1234-123456789123",
    "aliases" : [
      "$RCAnonymousID:12345678-1234-1234-1234-123456789123"
    ],
    "offer_code": null,
    "original_app_user_id" : "$RCAnonymousID:12345678-1234-1234-1234-123456789123",
    "new_product_id" : "com.revenuecat.myapp.yearly",
    "currency" : "USD",
    "price" : 0,
    "price_in_purchased_currency" : 0,
    "subscriber_attributes" : {},
    "store" : "PLAY_STORE",
    "takehome_percentage" : 0.70,
    "tax_percentage": 0.0,
    "commission_percentage": 0.3,
    "type" : "PRODUCT_CHANGE",
    "id" : "12345678-1234-1234-1234-12345678912",
    "app_id" : "1234567890",
    "experiments": [
      {
        "experiment_id": "prexp123",
        "experiment_variant": "b",
        "enrolled_at_ms": 1762274791000
      }
    ]
  },
  "api_version" : "1.0"
}
```

**Subscription Extended**

```json
{
    "event": {
      "event_timestamp_ms": 1697451462232,
      "product_id": "com.subscription.weekly",
      "period_type": "NORMAL",
      "purchased_at_ms": 1696846623000,
      "expiration_at_ms": 1697451423000,
      "environment": "PRODUCTION",
      "entitlement_id": null,
      "entitlement_ids": ["pro"],
      "presented_offering_id": null,
      "transaction_id": "123456789012345",
      "original_transaction_id": "123456789012345",
      "is_family_share": false,
      "country_code": "US",
      "app_user_id": "1234567890",
      "aliases": ["$RCAnonymousID:8069238d6049ce87cc529853916d624c"],
      "original_app_user_id": "$RCAnonymousID:8069238d6049ce87cc529853916d624c",
      "currency": "USD",
      "price": 0.00,
      "price_in_purchased_currency": 0.00,
      "subscriber_attributes": {
          "$email": {
              "updated_at_ms": 1662955084635,
              "value": "firstlast@gmail.com"
          }
      },
      "store": "APP_STORE",
      "takehome_percentage": 0.7,
      "offer_code": null,
      "tax_percentage": 0.012,
      "commission_percentage": 0.3,
      "type": "SUBSCRIPTION_EXTENDED",
      "id": "12345678-1234-1234-1234-123456789012",
      "app_id": "1234567890",
      "experiments": [
          {
              "experiment_id": "prexp123",
              "experiment_variant": "b",
              "enrolled_at_ms": 1762274791000
          }
      ]
    },
    "api_version": "1.0"
  }
```

Here are some events you will see regarding trials:

**Trial Started**

```json
{
    "event": {
        "event_timestamp_ms": 1658726366696,
        "product_id": "com.subscription.yearly",
        "period_type": "TRIAL",
        "purchased_at_ms": 1658726358573,
        "expiration_at_ms": 1658992117958,
        "environment": "PRODUCTION",
        "entitlement_id": null,
        "entitlement_ids": [
            "pro"
        ],
        "presented_offering_id": "standard",
        "transaction_id": "123456789012345",
        "original_transaction_id": "123456789012345",
        "is_family_share": false,
        "country_code": "PH",
        "app_user_id": "1234567890",
        "aliases": [
            "$RCAnonymousID:8069238d6049ce87cc529853916d624c"
        ],
        "original_app_user_id": "$RCAnonymousID:87c6049c58069238dce29853916d624c",
        "currency": "PHP",
        "price": 0,
        "price_in_purchased_currency": 0,
        "subscriber_attributes": {
            "$email": {
                "updated_at_ms": 1662955084635,
                "value": "firstlast@gmail.com"
            }
        },
        "store": "PLAY_STORE",
        "takehome_percentage": 0.85,
        "tax_percentage": 0.0,
        "commission_percentage": 0.15,
        "offer_code": null,
        "type": "INITIAL_PURCHASE",
        "id": "12345678-1234-1234-1234-123456789012",
        "app_id": "1234567890",
        "experiments": [
            {
                "experiment_id": "prexp123",
                "experiment_variant": "b",
                "enrolled_at_ms": 1762274791000
            }
        ]
    },
    "api_version": "1.0"
}
```

**Trial Cancelled**

```json
{
    "event": {
        "event_timestamp_ms": 1658726482659,
        "product_id": "com.subscription.weekly",
        "period_type": "TRIAL",
        "purchased_at_ms": 1658725349000,
        "expiration_at_ms": 1658984549000,
        "environment": "PRODUCTION",
        "entitlement_id": null,
        "entitlement_ids": [
            "Premium"
        ],
        "presented_offering_id": null,
        "transaction_id": "123456789012345",
        "original_transaction_id": "123456789012345",
        "is_family_share": false,
        "country_code": "CN",
        "app_user_id": "1234567890",
        "aliases": [
            "$RCAnonymousID:8069238d6049ce87cc529853916d624c"
        ],
        "original_app_user_id": "$RCAnonymousID:87c6049c58069238dce29853916d624c",
        "cancel_reason": "UNSUBSCRIBE",
        "currency": "CNY",
        "price": 0.0,
        "price_in_purchased_currency": 0.0,
        "subscriber_attributes": {
            "$email": {
                "updated_at_ms": 1662955084635,
                "value": "firstlast@gmail.com"
            }
        },
        "store": "APP_STORE",
        "takehome_percentage": 0.7,
        "tax_percentage": 0.0,
        "commission_percentage": 0.3,
        "offer_code": null,
        "type": "CANCELLATION",
        "id": "12345678-1234-1234-1234-123456789012",
        "app_id": "1234567890",
        "experiments": [
            {
                "experiment_id": "prexp123",
                "experiment_variant": "b",
                "enrolled_at_ms": 1762274791000
            }
        ]
    },
    "api_version": "1.0"
}
```

When a RevenueCat Billing invoices is issued, a INVOICE_ISSUANCE webhook is created:

**Invoice Issued**

```json
{
  "event": {
    "event_timestamp_ms": 1745004447300,
    "product_id": "com.subscription.monthly",
    "period_type": null,
    "purchased_at_ms": 1745004447283,
    "expiration_at_ms": null,
    "environment": "PRODUCTION",
    "entitlement_id": null,
    "entitlement_ids": null,
    "presented_offering_id": null,
    "transaction_id": null,
    "original_transaction_id": null,
    "is_family_share": false,
    "country_code": "US",
    "app_user_id": "41234567890",
    "aliases": ["$RCAnonymousID:8069238d6049ce87cc529853916d624c"],
    "original_app_user_id": "41234567890",
    "currency": "USD",
    "price_in_purchased_currency": 9.0,
    "subscriber_attributes": {
        "$email": {
            "updated_at_ms": 1662955084635,
            "value": "firstlast@gmail.com"
        }
    },
    "store": "RC_BILLING",
    "takehome_percentage": null,
    "offer_code": null,
    "tax_percentage": null,
    "commission_percentage": null,
    "metadata": null,
    "renewal_number": null,
    "type": "INVOICE_ISSUANCE",
    "id": "12345678-1234-1234-1234-123456789012",
    "app_id": "1234567890"
  },
  "api_version": "1.0"
}
```

In cases when the stores are down, RevenueCat may grant a [temporary entitlement](https://www.revenuecat.com/docs/integrations/webhooks/event-types-and-fields#event-types) until the stores are operational.

**Temporary Entitlement Grant**

```json
{
  "event": {
    "event_timestamp_ms": 1744824815307,
    "app_user_id": "41234567890",
    "store": "APP_STORE",
    "type": "TEMPORARY_ENTITLEMENT_GRANT",
    "id": "12345678-1234-1234-1234-123456789012",
    "app_id": "1234567890"
  },
  "api_version": "1.0"
}
```

When an in-app currency transaction occurs, a VIRTUAL_CURRENCY_TRANSACTION webhook is created:

**In-App Currency Transaction**

```json
{
    "event": {
      "adjustments": [
        {
          "amount": 100,
          "currency": {
            "code": "CRD",
            "description": "The main currency unit",
            "name": "Credits"
          }
        }
      ],
      "aliases": [
        "$RCAnonymousID:8069238d6049ce87cc529853916d624c"
      ],
      "app_id": "1234567890",
      "app_user_id": "1234567890",
      "event_timestamp_ms": 1658726378679,
      "product_display_name": "Monthly sub for 100 credits",
      "product_id": "1M_100credits",
      "purchase_environment": "PRODUCTION",
      "source": "in_app_purchase",
      "store": "APP_STORE",
      "subscriber_attributes": {
        "$email": {
            "updated_at_ms": 1662955084635,
            "value": "firstlast@gmail.com"
        }
      },
      "transaction_id": "123456789012345",
      "virtual_currency_transaction_id": "vatx123456789012345",
      "type": "VIRTUAL_CURRENCY_TRANSACTION",
      "id": "12345678-1234-1234-1234-123456789012"
    },
    "api_version": "1.0"
}
```

When an experiment enrollment occurs, an EXPERIMENT_ENROLLMENT webhook is created:

**Experiment Enrollment**

```json
{
    "event":{
       "event_timestamp_ms":1658726378679,
       "app_user_id":"$RCAnonymousID:12345678-1234-1234-1234-123456789123",
       "aliases":[
          "$RCAnonymousID:12345678-1234-1234-1234-123456789123"
       ],
       "original_app_user_id":"$RCAnonymousID:12345678-1234-1234-1234-123456789123",
       "experiment_id":"prexpca1234abcd",
       "experiment_variant":"b",
       "offering_id":"experiment_offering_b",
       "experiment_enrolled_at_ms":1658726378679,
       "type":"EXPERIMENT_ENROLLMENT",
       "id":"12345678-1234-1234-1234-123456789012"
    },
    "api_version":"1.0"
 }
```

When a Paddle, RevenueCat Billing, or Stripe purchase is redeemed in a native app, a PURCHASE_REDEEMED webhook is created. This fires in addition to any TRANSFER event when the redemption results in a transfer outcome:

**Purchase Redeemed**

```json
{
 "event": {
     "app_id": "1234567890",
     "event_timestamp_ms": 78789789798798,
     "id": "CD489E0E-5D52-4E03-966B-A7F17788E432",
     "store": "RC_BILLING",
     "environment": "PRODUCTION",
     "redeemed_from": ["$RCAnonymousID:abc123def4564789ab12345fedcba321"],
     "redeemed_by": ["redeemer-app-user-id"],
     "redemption_outcome": "alias",
     "redemption_platform": "ios",
     "product_id": "premium_monthly",
     "entitlement_ids": ["premium"],
     "workflow_id": "wf_abc123",
     "workflow_step_id": "step_xyz789",
     "trace_id": "trace_abcdef",
     "type": "PURCHASE_REDEEMED"
 },
 "api_version": "1.0"
}
```
