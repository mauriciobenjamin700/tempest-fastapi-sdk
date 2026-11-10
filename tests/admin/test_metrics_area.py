"""Tests for the admin indicator areas (#732)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from tempest_fastapi_sdk import (
    AdminSite,
    AdminSurfaceEnum,
    AsyncDatabaseManager,
    BaseUserModel,
    MetricLevelEnum,
    MetricsArea,
    MetricTrend,
    Threshold,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.testing import admin_login


class AreaUser(BaseUserModel):
    __tablename__ = "admin_area_users"


SECRET = "x" * 48


class RideMetricsSchema(BaseModel):
    """Indicators of the ride service."""

    messages_to_route: float = Field(
        title="Messages until the route",
        description="Average messages before the user found the route.",
    )
    accesses: int = Field(title="Accesses")
    accounts_created: int = Field(title="Accounts created")
    rides_created: int = Field(title="Rides created")
    revenue: MetricTrend = Field(title="Revenue")


class RideMetricsService:
    """A service whose method takes no arguments."""

    def __init__(self, session: AsyncSession) -> None:
        """Keep the session the admin hands over.

        Args:
            session (AsyncSession): The admin's session.
        """
        self.session = session

    async def collect(self) -> RideMetricsSchema:
        """Return fixed indicators.

        Returns:
            RideMetricsSchema: The indicators.
        """
        assert isinstance(self.session, AsyncSession)
        return RideMetricsSchema(
            messages_to_route=7.5,
            accesses=1200,
            accounts_created=45,
            rides_created=100,
            revenue=MetricTrend(value=90.0, previous=100.0, unit="BRL"),
        )


class BrokenService:
    """A service that fails to collect."""

    def __init__(self, session: AsyncSession) -> None:
        """Ignore the session.

        Args:
            session (AsyncSession): The admin's session.
        """

    def collect(self) -> RideMetricsSchema:
        """Fail.

        Raises:
            RuntimeError: Always.
        """
        raise RuntimeError("collector blew up")


def _rides_area() -> MetricsArea:
    """Return the area the page tests render.

    Returns:
        MetricsArea: The rides area.
    """
    return MetricsArea(
        "Rides",
        RideMetricsSchema,
        provider=RideMetricsService,
        method="collect",
        thresholds={
            "messages_to_route": Threshold(green=3, yellow=6, lower_is_better=True),
            "accounts_created": Threshold(green=100, yellow=40),
            "rides_created": Threshold(green=100, yellow=40),
            "revenue": Threshold(green=100, yellow=80),
        },
        units={"rides_created": "rides"},
    )


async def _app(
    site: AdminSite, **router_options: object
) -> tuple[FastAPI, AsyncDatabaseManager]:
    """Build an admin app with one admin user.

    Args:
        site (AdminSite): The site to mount.
        **router_options (object): Extra ``make_admin_router`` arguments.

    Returns:
        tuple[FastAPI, AsyncDatabaseManager]: The app and its database.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()
    async with db.get_session_context() as session:
        user = AreaUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        await session.commit()
    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(AreaUser),
            secret_key=SECRET,
            cookie_secure=False,
            show_metrics=False,
            **router_options,  # type: ignore[arg-type]
        )
    )
    return app, db


@pytest.fixture
async def rides_app() -> AsyncIterator[FastAPI]:
    """Admin with the rides area and a broken one.

    Yields:
        FastAPI: The app.
    """
    site = AdminSite(
        title="Transport",
        metrics_areas=[
            _rides_area(),
            MetricsArea(
                "Broken", RideMetricsSchema, provider=BrokenService, method="collect"
            ),
        ],
    )
    app, db = await _app(site)
    yield app
    await db.drop_tables()
    await db.disconnect()


def _client(app: FastAPI) -> AsyncClient:
    """Return an HTTP client for ``app``.

    Args:
        app (FastAPI): The app.

    Returns:
        AsyncClient: The client.
    """
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestThreshold:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (150, MetricLevelEnum.GREEN),
            (100, MetricLevelEnum.GREEN),
            (99.9, MetricLevelEnum.YELLOW),
            (40, MetricLevelEnum.YELLOW),
            (39.9, MetricLevelEnum.RED),
        ],
    )
    def test_higher_is_better_bands_are_inclusive_on_the_better_side(
        self, value: float, expected: MetricLevelEnum
    ) -> None:
        """Cenário: valor nos limites e entre eles, maior é melhor.

        Esperado: o limite exato cai na faixa melhor.
        """
        assert Threshold(green=100, yellow=40).level(value) is expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (1, MetricLevelEnum.GREEN),
            (3, MetricLevelEnum.GREEN),
            (3.1, MetricLevelEnum.YELLOW),
            (6, MetricLevelEnum.YELLOW),
            (6.1, MetricLevelEnum.RED),
        ],
    )
    def test_lower_is_better_flips_the_comparison(
        self, value: float, expected: MetricLevelEnum
    ) -> None:
        """Cenário: mensagens até achar a rota, menos é melhor.

        Esperado: até 3 verde, até 6 amarelo, acima vermelho.
        """
        threshold = Threshold(green=3, yellow=6, lower_is_better=True)
        assert threshold.level(value) is expected

    @pytest.mark.parametrize(
        ("green", "yellow", "lower_is_better"),
        [(40, 100, False), (6, 3, True)],
    )
    def test_inverted_limits_are_refused(
        self, green: float, yellow: float, lower_is_better: bool
    ) -> None:
        """Cenário: limites na ordem errada para a direção.

        Esperado: ``ValueError`` na construção.
        """
        with pytest.raises(ValueError, match="green"):
            Threshold(green=green, yellow=yellow, lower_is_better=lower_is_better)


class TestMetricsAreaDeclaration:
    def test_a_threshold_on_an_unknown_field_is_refused(self) -> None:
        """Cenário: typo no nome do campo do threshold.

        Esperado: ``ValueError`` nomeando o campo, em vez de card sem cor.
        """
        with pytest.raises(ValueError, match="rides_criated"):
            MetricsArea(
                "Rides",
                RideMetricsSchema,
                provider=RideMetricsService,
                method="collect",
                thresholds={"rides_criated": Threshold(green=1, yellow=0)},
            )

    def test_two_areas_with_the_same_slug_are_refused(self) -> None:
        """Cenário: duas áreas cujo título vira o mesmo slug.

        Esperado: ``ValueError`` no ``AdminSite``.
        """
        with pytest.raises(ValueError, match="rides"):
            AdminSite(metrics_areas=[_rides_area(), _rides_area()])

    def test_the_slug_comes_from_the_title(self) -> None:
        """Cenário: título com espaço e maiúscula.

        Esperado: slug minúsculo com hífen.
        """
        area = MetricsArea(
            "Rides Today",
            RideMetricsSchema,
            provider=RideMetricsService,
            method="collect",
        )
        assert area.slug == "rides-today"


@pytest.mark.asyncio
class TestMetricsAreaPage:
    async def test_one_card_per_field_with_the_level_of_its_threshold(
        self, rides_app: FastAPI
    ) -> None:
        """Cenário: a página da área com o service fake.

        Esperado: um card por campo do schema, na ordem dos campos, com o
        ``data-level`` do threshold — 7,5 mensagens é vermelho (menos é
        melhor, amarelo até 6), 45 contas amarelo, 100 caronas verde no
        limite, a receita de tendência 90 amarelo — e o campo sem threshold
        sem nível.
        """
        async with _client(rides_app) as client:
            await admin_login(client, "root@example.com", "hunter2")
            response = await client.get("/admin/metrics/rides")
        assert response.status_code == 200
        html = response.text
        labels = [
            "Messages until the route",
            "Accesses",
            "Accounts created",
            "Rides created",
            "Revenue",
        ]
        positions = [html.index(label) for label in labels]
        assert positions == sorted(positions)
        assert html.count('data-level="red"') == 1
        assert html.count('data-level="yellow"') == 2
        assert html.count('data-level="green"') == 1
        assert html.count("tempest-admin-card tempest-admin-card--") == 5
        assert "Average messages before the user found the route." in html
        assert "rides</small>" in html

    async def test_the_sidebar_lists_the_areas(self, rides_app: FastAPI) -> None:
        """Cenário: o dashboard depois do login.

        Esperado: a seção Metrics na sidebar com o link de cada área.
        """
        async with _client(rides_app) as client:
            await admin_login(client, "root@example.com", "hunter2")
            response = await client.get("/admin/")
        assert 'href="/admin/metrics/rides"' in response.text
        assert 'href="/admin/metrics/broken"' in response.text

    async def test_a_failing_service_renders_the_error_not_a_500(
        self, rides_app: FastAPI, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Cenário: o método do service levanta.

        Esperado: 200 com a mensagem de erro, sem card, e o traceback no
        log nomeando a área.
        """
        with caplog.at_level(logging.ERROR):
            async with _client(rides_app) as client:
                await admin_login(client, "root@example.com", "hunter2")
                response = await client.get("/admin/metrics/broken")
        assert response.status_code == 200
        assert "could not be collected" in response.text
        assert "tempest-admin-card tempest-admin-card--" not in response.text
        assert any("Broken" in record.getMessage() for record in caplog.records)

    async def test_an_unknown_area_is_404(self, rides_app: FastAPI) -> None:
        """Cenário: slug que não existe.

        Esperado: 404.
        """
        async with _client(rides_app) as client:
            await admin_login(client, "root@example.com", "hunter2")
            response = await client.get("/admin/metrics/nope")
        assert response.status_code == 404

    async def test_the_surface_policy_gates_the_page_and_the_link(self) -> None:
        """Cenário: ``surface_policy`` que nega ``METRICS``.

        Esperado: 403 na página e nenhum link de área na sidebar.
        """

        def deny_metrics(_principal: object, surface: AdminSurfaceEnum) -> bool:
            """Deny the metrics surface only.

            Args:
                _principal (object): The admin.
                surface (AdminSurfaceEnum): The surface asked for.

            Returns:
                bool: False for ``METRICS``.
            """
            return surface is not AdminSurfaceEnum.METRICS

        app, db = await _app(
            AdminSite(metrics_areas=[_rides_area()]), surface_policy=deny_metrics
        )
        try:
            async with _client(app) as client:
                await admin_login(client, "root@example.com", "hunter2")
                page = await client.get("/admin/metrics/rides")
                index = await client.get("/admin/")
        finally:
            await db.drop_tables()
            await db.disconnect()
        assert page.status_code == 403
        assert "/admin/metrics/rides" not in index.text
