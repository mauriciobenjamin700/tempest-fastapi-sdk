"""What each fake does, and what it lets a test steer.

The signature guards live in ``test_fakes_contract.py``. Here the question is
the other half: does the fake hold state a flow can be asserted on, and can a
test reach the branch the real provider will not give it on demand.
"""

from __future__ import annotations

import pytest

from tempest_fastapi_sdk.genai.rag.schemas import SearchResult
from tempest_fastapi_sdk.geo.enums import TravelMode
from tempest_fastapi_sdk.geo.schemas import Coordinate
from tempest_fastapi_sdk.integrations.payment import (
    PaymentStatus,
    PixChargeRequest,
    PixEventType,
    PixProvider,
)
from tempest_fastapi_sdk.push.schemas import (
    PushDevice,
    PushPayloadSchema,
    PushPlatform,
)
from tempest_fastapi_sdk.testing.fakes import (
    FakeEmailUtils,
    FakeGeocodingBackend,
    FakeModerationBackend,
    FakePixProvider,
    FakePushDispatcher,
    FakeRoutingBackend,
    FakeTextBackend,
    FakeWebSearchBackend,
)

RECIFE = Coordinate(latitude=-8.05, longitude=-34.9)
OLINDA = Coordinate(latitude=-7.99, longitude=-34.85)


class TestFakePixProvider:
    async def test_the_whole_charge_cycle_runs_without_a_provider(self) -> None:
        """Create, read back, cancel — through the contract type."""
        provider: PixProvider = FakePixProvider()

        charge = await provider.create_pix_charge(
            PixChargeRequest(amount_cents=1990, reference="order-1"),
        )
        assert charge.status is PaymentStatus.PENDING
        assert charge.br_code

        same = await provider.get_pix_charge(charge.provider_charge_id)
        assert same.reference == "order-1"

        cancelled = await provider.cancel_pix_charge(charge.provider_charge_id)
        assert cancelled.status is PaymentStatus.CANCELLED

    async def test_advance_reaches_states_a_sandbox_cannot(self) -> None:
        """Paying and charging back are the point of the steering.

        Against a real provider, ``PAID`` needs somebody to scan a QR code
        and ``CHARGED_BACK`` needs somebody to open a dispute.
        """
        provider = FakePixProvider()
        charge = await provider.create_pix_charge(
            PixChargeRequest(amount_cents=500, reference="order-2"),
        )

        paid = provider.advance(charge.provider_charge_id, PaymentStatus.PAID)
        assert paid.type is PixEventType.CHARGE_PAID
        assert paid.charge is not None
        assert paid.charge.status is PaymentStatus.PAID

        reversed_ = provider.advance(
            charge.provider_charge_id,
            PaymentStatus.CHARGED_BACK,
        )
        assert reversed_.type is PixEventType.UNKNOWN
        assert reversed_.provider_event_name == "fake.charged_back"

    async def test_the_charges_view_is_read_only(self) -> None:
        """A test reads state without corrupting it by accident."""
        provider = FakePixProvider()
        await provider.create_pix_charge(
            PixChargeRequest(amount_cents=100, reference="order-3"),
        )

        assert len(provider.charges) == 1
        with pytest.raises(TypeError):
            provider.charges["x"] = None  # type: ignore[index]

    async def test_fail_next_raises_once(self) -> None:
        """The queued error fires on the next call, then it is gone."""
        provider = FakePixProvider()
        provider.fail_next(RuntimeError("provider down"))

        with pytest.raises(RuntimeError, match="provider down"):
            await provider.create_pix_charge(
                PixChargeRequest(amount_cents=100, reference="order-4"),
            )

        charge = await provider.create_pix_charge(
            PixChargeRequest(amount_cents=100, reference="order-5"),
        )
        assert charge.status is PaymentStatus.PENDING

    async def test_steering_does_not_consume_a_queued_failure(self) -> None:
        """``advance`` is the test acting, not the service calling."""
        provider = FakePixProvider()
        charge = await provider.create_pix_charge(
            PixChargeRequest(amount_cents=100, reference="order-6"),
        )
        provider.fail_next(RuntimeError("later"))

        provider.advance(charge.provider_charge_id, PaymentStatus.PAID)

        with pytest.raises(RuntimeError, match="later"):
            await provider.get_pix_charge(charge.provider_charge_id)


class TestFakeTextBackend:
    async def test_queued_replies_come_back_in_order(self) -> None:
        backend = FakeTextBackend()
        backend.queue("primeiro", "segundo")

        assert await backend.generate("a") == "primeiro"
        assert await backend.generate("b") == "segundo"

    async def test_the_default_reply_names_the_prompt(self) -> None:
        """An echo makes a failed assertion say which prompt produced it."""
        backend = FakeTextBackend()

        assert await backend.generate("resuma isto") == "[fake] resuma isto"

    async def test_chat_reads_the_last_message(self) -> None:
        backend = FakeTextBackend(default="ok")

        reply = await backend.chat(
            [
                {"role": "user", "content": "oi"},
                {"role": "assistant", "content": "olá"},
                {"role": "user", "content": "tudo bem?"},
            ],
        )

        assert reply == "ok"
        assert backend.prompts == ["tudo bem?"]

    async def test_stream_arrives_in_pieces(self) -> None:
        """Incremental rendering is exercised, not handed the whole string."""
        backend = FakeTextBackend()
        backend.queue("um dois tres")

        chunks = [chunk async for chunk in backend.stream("conte")]

        assert len(chunks) == 3
        assert "".join(chunks) == "um dois tres"


class TestFakeModerationBackend:
    async def test_clean_text_is_not_flagged(self) -> None:
        moderator = FakeModerationBackend()

        result = await moderator.check("bom dia")

        assert result.flagged is False
        assert result.score == 0.0
        assert result.categories == []

    async def test_registered_substrings_flag_case_insensitively(self) -> None:
        moderator = FakeModerationBackend()
        moderator.flag("idiota", category="insult")

        result = await moderator.check("Seu IDIOTA")

        assert result.flagged is True
        assert result.categories == ["insult"]
        assert result.score == pytest.approx(0.99)


class TestFakePushDispatcher:
    async def test_deliveries_are_recorded_not_sent(self) -> None:
        dispatcher = FakePushDispatcher()
        device = PushDevice(platform=PushPlatform.ANDROID, token="token-1")

        await dispatcher.send(device, PushPayloadSchema(title="Oi", body="Corpo"))

        assert len(dispatcher.sent) == 1
        assert dispatcher.sent_to("token-1")[0].payload.title == "Oi"
        assert dispatcher.sent_to("token-outro") == []

    async def test_a_retired_token_is_reachable_on_demand(self) -> None:
        """The branch that prunes a dead device, without waiting for FCM."""
        from tempest_fastapi_sdk.push.dispatcher import PushDeviceGoneError

        dispatcher = FakePushDispatcher()
        dispatcher.fail_next(PushDeviceGoneError("token retired"))
        device = PushDevice(platform=PushPlatform.IOS, token="token-2")

        with pytest.raises(PushDeviceGoneError):
            await dispatcher.send(device, PushPayloadSchema(title="Oi"))

        assert dispatcher.sent == []

    def test_claimed_platforms_default_to_all_of_them(self) -> None:
        assert FakePushDispatcher().platforms == frozenset(
            platform.value for platform in PushPlatform
        )


class TestFakeEmailUtils:
    async def test_messages_land_in_the_outbox(self) -> None:
        mailer = FakeEmailUtils()

        await mailer.send("ana@example.test", "Ativação", "Seu link")

        assert mailer.outbox[0].subject == "Ativação"
        assert mailer.outbox[0].to == ("ana@example.test",)

    async def test_a_single_recipient_and_a_list_are_both_normalized(self) -> None:
        mailer = FakeEmailUtils()

        await mailer.send(["a@example.test", "b@example.test"], "S", "B")

        assert mailer.outbox[0].to == ("a@example.test", "b@example.test")

    async def test_sent_to_searches_cc_and_bcc_too(self) -> None:
        mailer = FakeEmailUtils()

        await mailer.send("a@example.test", "S", "B", bcc=["auditor@example.test"])

        assert len(mailer.sent_to("auditor@example.test")) == 1

    async def test_fail_next_exercises_the_send_failure_branch(self) -> None:
        mailer = FakeEmailUtils()
        mailer.fail_next(RuntimeError("smtp down"))

        with pytest.raises(RuntimeError, match="smtp down"):
            await mailer.send("a@example.test", "S", "B")

        assert mailer.outbox == []


class TestFakeGeoBackends:
    async def test_geocode_answers_from_the_table(self) -> None:
        backend = FakeGeocodingBackend()
        backend.add_place("Recife", RECIFE, place_type="city")

        result = await backend.geocode("recife")

        assert result is not None
        assert result.coordinate == RECIFE
        assert result.place_type == "city"

    async def test_an_unknown_place_resolves_to_none(self) -> None:
        """The branch a service forgets: the address nobody can find."""
        backend = FakeGeocodingBackend()

        assert await backend.geocode("rua que nao existe") is None

    async def test_reverse_returns_the_nearest_registered_place(self) -> None:
        """A GPS reading never lands on a stored decimal."""
        backend = FakeGeocodingBackend()
        backend.add_place("Recife", RECIFE)
        backend.add_place("Olinda", OLINDA)

        result = await backend.reverse(
            Coordinate(latitude=-8.049, longitude=-34.899),
        )

        assert result is not None
        assert result.display_name == "Recife"

    async def test_routing_delegates_to_the_sdk_offline_estimator(self) -> None:
        """The numbers are the SDK's own, not arithmetic invented here."""
        from tempest_fastapi_sdk.geo.estimate import estimate_travel

        backend = FakeRoutingBackend()

        estimate = await backend.route(RECIFE, OLINDA, mode=TravelMode.CAR)
        expected = estimate_travel(RECIFE, OLINDA, TravelMode.CAR)

        assert estimate.distance_km == pytest.approx(expected.distance_km)
        assert estimate.duration_minutes == pytest.approx(expected.duration_minutes)
        assert estimate.source == "fake"

    async def test_a_pinned_route_wins(self) -> None:
        from tempest_fastapi_sdk.geo.schemas import TravelEstimate

        backend = FakeRoutingBackend()
        pinned = TravelEstimate(
            mode=TravelMode.CAR,
            distance_km=42.0,
            duration_minutes=7.0,
            source="pinned",
        )
        backend.add_route(RECIFE, OLINDA, pinned)

        assert await backend.route(RECIFE, OLINDA) == pinned


class TestFakeWebSearchBackend:
    async def test_results_come_from_the_table_and_respect_max_results(self) -> None:
        backend = FakeWebSearchBackend()
        backend.add_results(
            "pix",
            [
                SearchResult(
                    title=f"Resultado {index}",
                    url=f"https://example.test/{index}",
                    snippet="trecho",
                    content="conteudo",
                )
                for index in range(5)
            ],
        )

        results = await backend.search("PIX", max_results=2)

        assert len(results) == 2
        assert results[0].title == "Resultado 0"

    async def test_an_unregistered_query_returns_an_empty_list(self) -> None:
        """Empty is a valid search, not an error."""
        backend = FakeWebSearchBackend()

        assert await backend.search("nada", max_results=5) == []
