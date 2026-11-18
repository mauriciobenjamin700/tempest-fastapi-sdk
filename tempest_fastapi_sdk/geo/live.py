"""Live position: receive, throttle, keep the latest and deliver to an audience.

The glue a service otherwise writes by hand over ``cache`` + ``sse`` + ``geo``
every time someone moves and others watch — a driver on a trip, a courier on
a delivery, a vehicle in a fleet:

* :class:`LivePositionSchema` — what the moving device sends, validated:
  coordinates in range, ``recorded_at`` timezone-aware and not in the future
  (beyond :data:`LIVE_POSITION_MAX_CLOCK_SKEW`).
* :class:`LivePositionStore` — keeps **only the latest** position per
  channel in Redis, with a TTL, and decides whether a new one is worth
  keeping (``min_interval_seconds`` throttle, ``min_distance_m`` movement).
  An accepted position is published on an
  :class:`~tempest_fastapi_sdk.sse.SSEBroker` to the recipients an
  ``audience`` callback names, optionally with each recipient's ETA to their
  own target point.

Who may see a position is never the SDK's decision. Without an ``audience``
the store refuses to take a broker at all, so "deliver to everyone" cannot
happen by omission. ``redis`` is never imported here: the store takes the
client, like every Redis store the SDK ships.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

from pydantic import AwareDatetime, Field, ValidationError, field_validator

from tempest_fastapi_sdk.geo.distance import haversine_km
from tempest_fastapi_sdk.geo.enums import TravelModeEnum
from tempest_fastapi_sdk.geo.estimate import estimate_travel
from tempest_fastapi_sdk.geo.schemas import Coordinate
from tempest_fastapi_sdk.schemas.base import BaseSchema
from tempest_fastapi_sdk.utils import (
    LatitudeField,
    LongitudeField,
    NonNegativeFloatField,
)

if TYPE_CHECKING:
    from tempest_fastapi_sdk.geo.routing import RoutingBackend
    from tempest_fastapi_sdk.sse.broker import SSEBroker

logger: logging.Logger = logging.getLogger(__name__)

LIVE_POSITION_MAX_CLOCK_SKEW: Final[timedelta] = timedelta(seconds=30)
"""How far in the future ``recorded_at`` may be before it is refused.

A phone's clock drifts a few seconds from the server's; refusing anything
strictly ahead of ``now`` would drop honest positions. Thirty seconds
absorbs that drift and still refuses a timestamp that is plainly wrong.
"""

DEFAULT_LIVE_POSITION_KEY_PREFIX: Final[str] = "geo:live"
"""First segment of every Redis key :class:`LivePositionStore` writes."""

DEFAULT_LIVE_POSITION_TTL_SECONDS: Final[int] = 300
"""How long the latest position lives without a new one: five minutes.

Long enough to survive a tunnel or a dead zone, short enough that a phone
that stopped sending (app killed, trip abandoned without :meth:`clear`)
stops showing a stale marker on its own.
"""

DEFAULT_LIVE_POSITION_INTERVAL_SECONDS: Final[float] = 5.0
"""Least time between two accepted positions on one channel."""

DEFAULT_LIVE_POSITION_MIN_DISTANCE_M: Final[float] = 10.0
"""Least movement, in meters, for a new position to be accepted.

About the accuracy of a phone GPS in the open: below it, the "movement" is
mostly noise.
"""

DEFAULT_LIVE_POSITION_EVENT: Final[str] = "live_position"
"""SSE event name of an accepted position."""

DEFAULT_LIVE_POSITION_CLEARED_EVENT: Final[str] = "live_position_cleared"
"""SSE event name :meth:`LivePositionStore.clear` sends to the audience."""

AudienceResolver = Callable[[str], Awaitable[Sequence[str]]]
"""Maps a channel id to the SSE channels (usually user ids) that may see it."""

EtaTargetsResolver = Callable[[str], Awaitable[Mapping[str, Coordinate]]]
"""Maps a channel id to each recipient's target point (``{user_id: point}``)."""


class LivePositionSchema(BaseSchema):
    """One position reported by a moving device.

    Attributes:
        lat: Latitude in decimal degrees, within [-90, 90].
        lng: Longitude in decimal degrees, within [-180, 180].
        accuracy_m: Horizontal accuracy radius reported by the device, in
            meters.
        speed_kmh: Ground speed, in km/h.
        heading: Direction of travel in degrees clockwise from true north,
            within [0, 360).
        recorded_at: When the device took the fix. Timezone-aware; refused
            when more than :data:`LIVE_POSITION_MAX_CLOCK_SKEW` ahead of the
            server clock.
    """

    lat: LatitudeField = Field(
        title="Latitude",
        description="Latitude in decimal degrees (WGS84).",
        examples=[-5.0892],
    )
    lng: LongitudeField = Field(
        title="Longitude",
        description="Longitude in decimal degrees (WGS84).",
        examples=[-42.8019],
    )
    accuracy_m: NonNegativeFloatField | None = Field(
        default=None,
        title="Accuracy (m)",
        description="Horizontal accuracy radius reported by the device.",
        examples=[8.0],
    )
    speed_kmh: NonNegativeFloatField | None = Field(
        default=None,
        title="Speed (km/h)",
        description="Ground speed reported by the device.",
        examples=[42.0],
    )
    heading: float | None = Field(
        default=None,
        ge=0,
        lt=360,
        title="Heading",
        description="Degrees clockwise from true north, in [0, 360).",
        examples=[87.5],
    )
    recorded_at: AwareDatetime = Field(
        title="Recorded at",
        description="When the device took the fix (timezone-aware).",
    )

    @field_validator("recorded_at")
    @classmethod
    def _not_in_future(cls, value: datetime) -> datetime:
        """Refuse a fix stamped further ahead than the allowed clock skew.

        Args:
            value (datetime): The parsed, timezone-aware timestamp.

        Returns:
            datetime: ``value`` unchanged.

        Raises:
            ValueError: When ``value`` is more than
                :data:`LIVE_POSITION_MAX_CLOCK_SKEW` in the future.
        """
        if value > datetime.now(UTC) + LIVE_POSITION_MAX_CLOCK_SKEW:
            raise ValueError("recorded_at is in the future")
        return value

    @property
    def coordinate(self) -> Coordinate:
        """Return the position as a :class:`Coordinate`.

        Returns:
            Coordinate: ``lat``/``lng`` as ``latitude``/``longitude``.
        """
        return Coordinate(latitude=self.lat, longitude=self.lng)


class LiveEtaSchema(BaseSchema):
    """Distance and time from the live position to one recipient's target.

    Attributes:
        target: The recipient's target point (a boarding stop, a door).
        distance_km: Road distance to the target, in kilometers.
        duration_minutes: Travel time to the target, in minutes.
        source: ``"osrm"`` (or whatever the routing backend stamps) when the
            matrix answered; ``"heuristic"`` when there is no backend or it
            failed and :func:`~tempest_fastapi_sdk.geo.estimate_travel`
            stood in.
    """

    target: Coordinate = Field(
        title="Target",
        description="The recipient's target point.",
    )
    distance_km: NonNegativeFloatField = Field(
        title="Distance (km)",
        description="Road distance to the target.",
        examples=[3.4],
    )
    duration_minutes: NonNegativeFloatField = Field(
        title="Duration (min)",
        description="Travel time to the target.",
        examples=[7.5],
    )
    source: str = Field(
        title="Source",
        description='How the ETA was produced ("osrm" or "heuristic").',
        examples=["osrm"],
    )


class LivePositionEventSchema(BaseSchema):
    """The SSE payload one recipient gets for an accepted position.

    Attributes:
        channel_id: The channel (trip, delivery, vehicle) the position
            belongs to.
        position: The accepted position.
        eta: This recipient's ETA to their own target, when the store was
            given ``eta_targets`` and the recipient has a target.
    """

    channel_id: str = Field(
        title="Channel id",
        description="The channel the position belongs to.",
        examples=["trip-42"],
    )
    position: LivePositionSchema = Field(
        title="Position",
        description="The accepted position.",
    )
    eta: LiveEtaSchema | None = Field(
        default=None,
        title="ETA",
        description="This recipient's ETA to their target, when computed.",
    )


@runtime_checkable
class _LivePositionRedisClient(Protocol):
    """The subset of ``redis.asyncio.Redis`` :class:`LivePositionStore` uses.

    Positional-only parameters and ``Awaitable`` returns, because the client
    we do not own names its parameters ``name``/``names``/``time``: spelling
    the contract our way would reject the very client the docs tell the
    reader to pass. Nothing here imports ``redis``.
    """

    def get(self, key: str, /) -> Awaitable[str | bytes | None]:
        """Return the value under ``key``, or ``None`` when absent.

        Args:
            key: The key to read.

        Returns:
            The stored value, as text or bytes, or ``None``.
        """
        ...

    def set(self, key: str, value: str, /, *, ex: int) -> Awaitable[object]:
        """Store ``value`` under ``key``, expiring after ``ex`` seconds.

        Args:
            key: The key to write.
            value: The value to keep.
            ex: Time-to-live in seconds, the name redis-py uses.

        Returns:
            Whatever the client answers; the store discards it.
        """
        ...

    def expire(self, key: str, seconds: int, /) -> Awaitable[object]:
        """Reset the time-to-live of ``key``.

        Args:
            key: The key to touch.
            seconds: The new time-to-live.

        Returns:
            Whatever the client answers; the store discards it.
        """
        ...

    def delete(self, key: str, /) -> Awaitable[object]:
        """Remove ``key``.

        Args:
            key: The key to remove.

        Returns:
            Whatever the client answers; the store discards it.
        """
        ...


class LivePositionStore:
    """Latest position per channel in Redis, delivered to a resolved audience.

    Takes the client rather than a manager: pass
    ``AsyncRedisManager.client_proxy`` when the store is built before the
    lifespan runs, or ``AsyncRedisManager.client`` once inside.

    The acceptance check reads the previous position and then writes the new
    one, two round trips with no lock between them. Two positions for the
    same channel arriving at the same instant can both pass the throttle;
    that costs one extra event, never a wrong position, and a channel has a
    single sender in the use this is built for.

    Attributes:
        key_prefix (str): First segment of every key.
        ttl_seconds (int): Lifetime of the latest position.
        min_interval_seconds (float): Throttle between accepted positions.
        min_distance_m (float): Least movement for a position to be accepted.
        event_name (str): SSE event name of an accepted position.
        cleared_event_name (str): SSE event name :meth:`clear` sends.
        mode (TravelModeEnum): Travel mode of the ETA.
    """

    def __init__(
        self,
        redis: _LivePositionRedisClient,
        *,
        key_prefix: str = DEFAULT_LIVE_POSITION_KEY_PREFIX,
        ttl_seconds: int = DEFAULT_LIVE_POSITION_TTL_SECONDS,
        min_interval_seconds: float = DEFAULT_LIVE_POSITION_INTERVAL_SECONDS,
        min_distance_m: float = DEFAULT_LIVE_POSITION_MIN_DISTANCE_M,
        broker: SSEBroker | None = None,
        audience: AudienceResolver | None = None,
        event_name: str = DEFAULT_LIVE_POSITION_EVENT,
        cleared_event_name: str = DEFAULT_LIVE_POSITION_CLEARED_EVENT,
        eta_targets: EtaTargetsResolver | None = None,
        routing: RoutingBackend | None = None,
        mode: TravelModeEnum = TravelModeEnum.CAR,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Initialize the store.

        Args:
            redis (_LivePositionRedisClient): Async Redis client (from the
                ``[cache]`` extra) or a compatible one.
            key_prefix (str): First segment of every key
                (``"<prefix>:<channel_id>"``). Give each service its own when
                they share a Redis.
            ttl_seconds (int): Lifetime of the latest position, renewed by
                every accepted position and by every refused-as-too-close
                one, so a sender standing still stays visible.
            min_interval_seconds (float): Least server time between two
                accepted positions on one channel. ``0`` disables the
                throttle.
            min_distance_m (float): Least movement from the latest accepted
                position. ``0`` disables the check.
            broker (SSEBroker | None): Where accepted positions are
                published. ``None`` keeps the store silent (Redis only).
            audience (AudienceResolver | None): Called with the channel id
                on every delivery; answers the broker channels (usually user
                ids, the default channel of
                :func:`~tempest_fastapi_sdk.make_sse_router`) allowed to see
                it. Required with ``broker``; an empty answer delivers to
                nobody.
            event_name (str): SSE event name of an accepted position.
            cleared_event_name (str): SSE event name :meth:`clear` sends.
            eta_targets (EtaTargetsResolver | None): Called with the channel
                id; answers each recipient's target point. Turns the ETA on.
                A recipient it names that ``audience`` does not is ignored:
                ``audience`` is the only authority on who sees what.
            routing (RoutingBackend | None): Backend whose ``matrix`` computes
                the ETA in one request. ``None``, or a failing backend, falls
                back to :func:`~tempest_fastapi_sdk.geo.estimate_travel`.
            mode (TravelModeEnum): Travel mode of the ETA.
            clock (Callable[[], float]): Source of server time, in seconds
                since the epoch, for the throttle. Defaults to
                :func:`time.time`, comparable across workers.

        Raises:
            ValueError: When ``broker`` and ``audience`` are not given
                together, when ``eta_targets`` or ``routing`` is given
                without a broker or without ``eta_targets``, or when a
                threshold is negative or ``ttl_seconds`` is lower than 1.
        """
        if (broker is None) != (audience is None):
            raise ValueError(
                "broker and audience go together: the SDK never decides who "
                "may see a position, so a broker needs an audience callback",
            )
        if eta_targets is not None and broker is None:
            raise ValueError("eta_targets needs a broker to deliver the ETA")
        if routing is not None and eta_targets is None:
            raise ValueError("routing is only used for the ETA; pass eta_targets")
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be at least 1")
        if min_interval_seconds < 0 or min_distance_m < 0:
            raise ValueError("min_interval_seconds and min_distance_m must be >= 0")
        self._redis: _LivePositionRedisClient = redis
        self.key_prefix: str = key_prefix
        self.ttl_seconds: int = ttl_seconds
        self.min_interval_seconds: float = min_interval_seconds
        self.min_distance_m: float = min_distance_m
        self._broker: SSEBroker | None = broker
        self._audience: AudienceResolver | None = audience
        self.event_name: str = event_name
        self.cleared_event_name: str = cleared_event_name
        self._eta_targets: EtaTargetsResolver | None = eta_targets
        self._routing: RoutingBackend | None = routing
        self.mode: TravelModeEnum = mode
        self._clock: Callable[[], float] = clock

    def _key(self, channel_id: str) -> str:
        """Return the Redis key of ``channel_id``.

        Args:
            channel_id (str): The channel.

        Returns:
            str: ``"<key_prefix>:<channel_id>"``.
        """
        return f"{self.key_prefix}:{channel_id}"

    async def _read(self, channel_id: str) -> tuple[LivePositionSchema, float] | None:
        """Return the stored position and the server time it was accepted.

        A value that does not decode is logged (channel only, never the
        payload) and treated as absent, so one corrupt key cannot wedge a
        channel: the next position overwrites it.

        Args:
            channel_id (str): The channel.

        Returns:
            tuple[LivePositionSchema, float] | None: The position and its
            acceptance time, or ``None`` when there is none.
        """
        raw = await self._redis.get(self._key(channel_id))
        if raw is None:
            return None
        try:
            payload: Any = json.loads(raw)
            position = LivePositionSchema.model_validate(payload["position"])
            received_at = float(payload["received_at"])
        except (ValueError, KeyError, TypeError, ValidationError):
            logger.warning(
                "LivePositionStore ignored an undecodable value for %r", channel_id
            )
            return None
        return position, received_at

    async def latest(self, channel_id: str) -> LivePositionSchema | None:
        """Return the latest accepted position of ``channel_id``.

        Args:
            channel_id (str): The channel.

        Returns:
            LivePositionSchema | None: The position, or ``None`` when there
            is none (never sent, cleared, or expired).
        """
        stored = await self._read(channel_id)
        return None if stored is None else stored[0]

    async def publish(self, channel_id: str, position: LivePositionSchema) -> bool:
        """Keep ``position`` as the latest of ``channel_id`` when it is worth it.

        Refused, in order, when: it is not newer than the stored one
        (``recorded_at`` not after it — a late or replayed fix); less than
        ``min_interval_seconds`` of server time passed since the last
        accepted one; it moved less than ``min_distance_m``. A position
        refused for distance still renews the TTL, so a sender standing at a
        stop does not vanish. An accepted position is written with the TTL
        and, with a broker, delivered to the audience.

        Args:
            channel_id (str): The channel (trip, delivery, vehicle).
            position (LivePositionSchema): The reported position.

        Returns:
            bool: Whether the position was accepted.
        """
        now = self._clock()
        key = self._key(channel_id)
        stored = await self._read(channel_id)
        if stored is not None:
            previous, received_at = stored
            if position.recorded_at <= previous.recorded_at:
                return False
            if now - received_at < self.min_interval_seconds:
                return False
            moved_m = haversine_km(previous.coordinate, position.coordinate) * 1000.0
            if moved_m < self.min_distance_m:
                await self._redis.expire(key, self.ttl_seconds)
                return False
        value = json.dumps(
            {"position": position.model_dump(mode="json"), "received_at": now},
        )
        await self._redis.set(key, value, ex=self.ttl_seconds)
        await self._deliver(channel_id, position)
        return True

    async def clear(self, channel_id: str) -> None:
        """End tracking of ``channel_id``: forget the position, tell the audience.

        Call it when the trip (delivery, shift) ends. The latest position is
        deleted at once instead of lingering until the TTL, and, with a
        broker, every recipient gets :attr:`cleared_event_name` with
        ``{"channel_id": ...}`` so the client removes the marker.

        Args:
            channel_id (str): The channel.
        """
        await self._redis.delete(self._key(channel_id))
        if self._broker is None or self._audience is None:
            return
        for recipient in await self._recipients(channel_id):
            await self._broker.publish(
                recipient,
                {"channel_id": channel_id},
                event=self.cleared_event_name,
            )

    async def _recipients(self, channel_id: str) -> list[str]:
        """Return the audience of ``channel_id``, deduplicated, in order.

        Args:
            channel_id (str): The channel.

        Returns:
            list[str]: The broker channels to deliver to.
        """
        if self._audience is None:
            return []
        return list(dict.fromkeys(await self._audience(channel_id)))

    async def _deliver(self, channel_id: str, position: LivePositionSchema) -> None:
        """Publish an accepted position to each recipient, with its ETA.

        Args:
            channel_id (str): The channel.
            position (LivePositionSchema): The accepted position.
        """
        if self._broker is None:
            return
        recipients = await self._recipients(channel_id)
        if not recipients:
            return
        etas = await self._etas(channel_id, position, recipients)
        for recipient in recipients:
            event = LivePositionEventSchema(
                channel_id=channel_id,
                position=position,
                eta=etas.get(recipient),
            )
            await self._broker.publish(
                recipient,
                event.model_dump(mode="json"),
                event=self.event_name,
            )

    async def _etas(
        self,
        channel_id: str,
        position: LivePositionSchema,
        recipients: list[str],
    ) -> dict[str, LiveEtaSchema]:
        """Compute each recipient's ETA to their own target.

        One ``matrix`` request covers every distinct target. When the
        backend is absent or raises, :func:`estimate_travel` answers per
        target and the failure is logged (type only).

        Args:
            channel_id (str): The channel.
            position (LivePositionSchema): The accepted position.
            recipients (list[str]): The audience; targets of anyone else are
                dropped.

        Returns:
            dict[str, LiveEtaSchema]: ETA per recipient that has a target.
        """
        if self._eta_targets is None:
            return {}
        allowed = set(recipients)
        targets = {
            recipient: target
            for recipient, target in (await self._eta_targets(channel_id)).items()
            if recipient in allowed
        }
        if not targets:
            return {}
        points = list(
            dict.fromkeys((t.latitude, t.longitude) for t in targets.values())
        )
        coords = [Coordinate(latitude=lat, longitude=lng) for lat, lng in points]
        origin = position.coordinate
        per_point: dict[tuple[float, float], LiveEtaSchema] = {}
        if self._routing is not None:
            try:
                matrix = await self._routing.matrix([origin], coords, mode=self.mode)
                for index, point in enumerate(points):
                    per_point[point] = LiveEtaSchema(
                        target=coords[index],
                        distance_km=matrix.distances_km[0][index],
                        duration_minutes=matrix.durations_minutes[0][index],
                        source=matrix.source_label,
                    )
            except Exception as exc:
                logger.warning(
                    "LivePositionStore ETA fell back to estimate_travel: %s",
                    type(exc).__name__,
                )
                per_point = {}
        if not per_point:
            for index, point in enumerate(points):
                estimate = estimate_travel(origin, coords[index], self.mode)
                per_point[point] = LiveEtaSchema(
                    target=coords[index],
                    distance_km=estimate.distance_km,
                    duration_minutes=estimate.duration_minutes,
                    source=estimate.source,
                )
        return {
            recipient: per_point[(target.latitude, target.longitude)]
            for recipient, target in targets.items()
        }


__all__: list[str] = [
    "DEFAULT_LIVE_POSITION_CLEARED_EVENT",
    "DEFAULT_LIVE_POSITION_EVENT",
    "DEFAULT_LIVE_POSITION_INTERVAL_SECONDS",
    "DEFAULT_LIVE_POSITION_KEY_PREFIX",
    "DEFAULT_LIVE_POSITION_MIN_DISTANCE_M",
    "DEFAULT_LIVE_POSITION_TTL_SECONDS",
    "LIVE_POSITION_MAX_CLOCK_SKEW",
    "AudienceResolver",
    "EtaTargetsResolver",
    "LiveEtaSchema",
    "LivePositionEventSchema",
    "LivePositionSchema",
    "LivePositionStore",
]
