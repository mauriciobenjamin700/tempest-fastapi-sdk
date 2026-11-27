# `tempest_fastapi_sdk.push`

Push unificado: uma API para navegador e celular, com o model de token de dispositivo.

[« Tempo real e mensageria](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging.md)

---

- [`tempest_fastapi_sdk.push.service.DeviceService`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.service.DeviceService)
- [`tempest_fastapi_sdk.push.service.DeviceService.transport_for`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.service.DeviceService.transport_for)
- [`tempest_fastapi_sdk.push.service.DeviceService.register`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.service.DeviceService.register)
- [`tempest_fastapi_sdk.push.service.DeviceService.unregister`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.service.DeviceService.unregister)
- [`tempest_fastapi_sdk.push.service.DeviceService.list_for_user`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.service.DeviceService.list_for_user)
- [`tempest_fastapi_sdk.push.service.DeviceService.notify_user`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.service.DeviceService.notify_user)
- [`tempest_fastapi_sdk.push.service.DeviceService.to_device`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.service.DeviceService.to_device)
- [`tempest_fastapi_sdk.push.dispatcher.PushDispatcher`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.dispatcher.PushDispatcher)
- [`tempest_fastapi_sdk.push.dispatcher.PushDispatcher.send`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.dispatcher.PushDispatcher.send)
- [`tempest_fastapi_sdk.push.dispatcher.PushError`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.dispatcher.PushError)
- [`tempest_fastapi_sdk.push.dispatcher.PushDeviceGoneError`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.dispatcher.PushDeviceGoneError)
- [`tempest_fastapi_sdk.push.transports.webpush.WebPushTransport`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.transports.webpush.WebPushTransport)
- [`tempest_fastapi_sdk.push.transports.webpush.WebPushTransport.send`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.transports.webpush.WebPushTransport.send)
- [`tempest_fastapi_sdk.push.transports.fcm.FCMTransport`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.transports.fcm.FCMTransport)
- [`tempest_fastapi_sdk.push.transports.fcm.FCMTransport.send`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.transports.fcm.FCMTransport.send)
- [`tempest_fastapi_sdk.push.schemas.PushPlatformEnum`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.schemas.PushPlatformEnum)
- [`tempest_fastapi_sdk.push.schemas.PushDevice`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.schemas.PushDevice)
- [`tempest_fastapi_sdk.push.schemas.PushDevice.masked_token`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.schemas.PushDevice.masked_token)
- [`tempest_fastapi_sdk.push.schemas.PushPayloadSchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.schemas.PushPayloadSchema)
- [`tempest_fastapi_sdk.push.schemas.DeviceRegistrationSchema`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.schemas.DeviceRegistrationSchema)
- [`tempest_fastapi_sdk.push.schemas.PushResult`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.schemas.PushResult)
- [`tempest_fastapi_sdk.push.schemas.PushFanoutResult`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.schemas.PushFanoutResult)
- [`tempest_fastapi_sdk.push.schemas.PushFanoutResult.delivered`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.schemas.PushFanoutResult.delivered)
- [`tempest_fastapi_sdk.push.schemas.PushFanoutResult.pruned`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.schemas.PushFanoutResult.pruned)
- [`tempest_fastapi_sdk.push.schemas.PushFanoutResult.failed`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.schemas.PushFanoutResult.failed)
- [`tempest_fastapi_sdk.push.schemas.PushFanoutResult.as_dict`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.schemas.PushFanoutResult.as_dict)
- [`tempest_fastapi_sdk.push.schemas.mask_push_token`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.schemas.mask_push_token)
- [`tempest_fastapi_sdk.push.router.make_push_router`](https://mauriciobenjamin700.github.io/tempest-fastapi-sdk/reference/realtime-messaging/push/#tempest_fastapi_sdk.push.router.make_push_router)
