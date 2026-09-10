"""The admin's datetime widget reads and writes in a declared zone.

Without ``display_timezone`` the panel is a raw column editor: an
``<input type="datetime-local">`` carries no offset, so what the
operator types is stored verbatim and what the column holds (UTC) is
printed verbatim. In Brazil that is a three-hour error in both
directions, with nothing on the page naming a zone.

These tests pin the round trip by its effect — what a form renders, what
a submission stores, what the list and detail views print.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk import (
    AdminModel,
    AdminSite,
    AsyncDatabaseManager,
    BaseModel,
    BaseUserModel,
    UserModelAuthBackend,
    make_admin_router,
)
from tempest_fastapi_sdk.admin.forms import (
    FormField,
    build_form_fields,
    from_display_timezone,
    parse_submission,
    to_display_timezone,
)

SAO_PAULO = "America/Sao_Paulo"


class Meeting(BaseModel):
    """A row whose only interesting column is an aware timestamp."""

    __tablename__ = "admin_tz_meeting"

    title: Mapped[str] = mapped_column(String(64), nullable=False)
    starts_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


_utc: AdminModel[Meeting] = AdminModel(model=Meeting)
_local: AdminModel[Meeting] = AdminModel(
    model=Meeting,
    display_timezone=SAO_PAULO,
)


def _field(
    admin: AdminModel[Meeting],
    instance: Meeting,
    name: str,
) -> FormField:
    """Return one rendered form field by name.

    Args:
        admin (AdminModel[Meeting]): The admin being rendered.
        instance (Meeting): The row to pre-fill from.
        name (str): The field to pick.

    Returns:
        FormField: The matching descriptor.
    """
    rendered = build_form_fields(admin, instance=instance)
    return next(f for f in rendered if f.name == name)


class TestDeclaringTheZone:
    """``display_timezone`` is validated where it is declared."""

    def test_an_unknown_zone_fails_at_declaration(self) -> None:
        """A typo must not survive until a request renders the form.

        The cost of resolving it lazily is the shape the cron offset had
        (v0.284.0): a string that only fails deep inside a loop, at a
        moment nobody is watching.
        """
        with pytest.raises(ValueError, match="not a zone"):
            AdminModel(model=Meeting, display_timezone="America/Sao_Paolo")

    def test_none_keeps_utc(self) -> None:
        """The default is unchanged behavior: no conversion anywhere."""
        assert _utc.display_tzinfo is None
        assert _utc.display_timezone is None


class TestTheFormRoundTrip:
    """What the operator reads is what the operator typed."""

    def test_the_stored_instant_renders_in_the_zone(self) -> None:
        """23:00Z is 20:00 in São Paulo, and that is what the box shows."""
        instance = Meeting(
            title="Kickoff",
            starts_at=dt.datetime(2026, 6, 15, 23, 0, tzinfo=dt.UTC),
        )

        assert _field(_local, instance, "starts_at").value == "2026-06-15T20:00"
        assert _field(_utc, instance, "starts_at").value == "2026-06-15T23:00"

    def test_the_box_says_which_zone_it_means(self) -> None:
        """``datetime-local`` carries no offset, so the label must."""
        instance = Meeting(
            title="Kickoff",
            starts_at=dt.datetime(2026, 6, 15, 23, 0, tzinfo=dt.UTC),
        )

        assert _field(_local, instance, "starts_at").timezone == SAO_PAULO
        assert _field(_utc, instance, "starts_at").timezone is None

    def test_a_submitted_local_time_is_stored_as_utc(self) -> None:
        """The bug this closes: 20:00 typed, 20:00Z stored."""
        form = {
            "title": "Kickoff",
            "starts_at": "2026-06-15T20:00",
            "is_active": "true",
        }

        data, errors = parse_submission(_local, form)

        assert errors == {}
        assert data["starts_at"] == dt.datetime(2026, 6, 15, 23, 0, tzinfo=dt.UTC)

    def test_without_a_zone_the_submission_stays_naive(self) -> None:
        """Unchanged default — the value is stored exactly as typed."""
        form = {
            "title": "Kickoff",
            "starts_at": "2026-06-15T20:00",
            "is_active": "true",
        }

        data, _errors = parse_submission(_utc, form)

        assert data["starts_at"] == dt.datetime(2026, 6, 15, 20, 0)

    def test_render_then_submit_is_the_identity(self) -> None:
        """Saving a form nobody edited must not move the instant.

        This is the property a half-conversion breaks: converting only
        on write turns every no-op save into a three-hour drift.
        """
        stored = dt.datetime(2026, 6, 15, 23, 0, tzinfo=dt.UTC)
        instance = Meeting(title="Kickoff", starts_at=stored)

        rendered = _field(_local, instance, "starts_at").value
        data, _errors = parse_submission(
            _local,
            {"title": "Kickoff", "starts_at": rendered, "is_active": "true"},
        )

        assert data["starts_at"] == stored

    def test_a_submitted_offset_is_honored_not_overridden(self) -> None:
        """A value that already names its instant is only converted."""
        form = {
            "title": "Kickoff",
            "starts_at": "2026-06-15T23:00:00+00:00",
            "is_active": "true",
        }

        data, _errors = parse_submission(_local, form)

        assert data["starts_at"] == dt.datetime(2026, 6, 15, 23, 0, tzinfo=dt.UTC)


class TestTheHelpers:
    """The two conversions, including the cases the column produces."""

    def test_a_naive_stored_value_is_read_as_utc(self) -> None:
        """``TIMESTAMP(timezone=True)`` answers naive on SQLite.

        Measured on this suite's database: the column is declared aware
        and the value comes back without ``tzinfo``. Treating it as
        local would move every row by the offset.
        """
        zone = ZoneInfo(SAO_PAULO)

        assert to_display_timezone(
            dt.datetime(2026, 6, 15, 23, 0),
            zone,
        ) == dt.datetime(2026, 6, 15, 20, 0, tzinfo=zone)

    def test_the_pair_is_a_round_trip_across_dst(self) -> None:
        """A zone with a historical DST change still round-trips.

        Brazil dropped DST in 2019, so a 2018 summer date and a 2026
        one resolve to different offsets in the same zone — the pair has
        to survive both rather than assume a fixed -03:00.
        """
        zone = ZoneInfo(SAO_PAULO)
        for moment in (
            dt.datetime(2018, 1, 15, 23, 0, tzinfo=dt.UTC),
            dt.datetime(2026, 6, 15, 23, 0, tzinfo=dt.UTC),
        ):
            local = to_display_timezone(moment, zone)
            assert from_display_timezone(local.replace(tzinfo=None), zone) == moment

    def test_a_2018_summer_instant_uses_the_dst_offset(self) -> None:
        """Pinning the offset that made the round trip worth testing."""
        zone = ZoneInfo(SAO_PAULO)

        local = to_display_timezone(
            dt.datetime(2018, 1, 15, 23, 0, tzinfo=dt.UTC),
            zone,
        )

        assert local.utcoffset() == dt.timedelta(hours=-2)


class ScheduledRun(BaseModel):
    """The row the end-to-end views below render."""

    __tablename__ = "admin_tz_run"

    label: Mapped[str] = mapped_column(String(64), nullable=False)
    runs_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class TzUser(BaseUserModel):
    """Admin principal for the end-to-end views."""

    __tablename__ = "admin_tz_users"


run_admin: AdminModel[ScheduledRun] = AdminModel(
    model=ScheduledRun,
    list_display=[ScheduledRun.label, ScheduledRun.runs_at],
    display_timezone=SAO_PAULO,
)

SECRET = "z" * 48
STORED = dt.datetime(2026, 6, 15, 23, 0, tzinfo=dt.UTC)


@pytest.fixture
async def panel() -> tuple[FastAPI, AsyncDatabaseManager, ScheduledRun]:
    """Serve the admin over a real ASGI app with one stored row.

    Yields:
        tuple[FastAPI, AsyncDatabaseManager, ScheduledRun]: The app, the
        database manager and the row the views render.
    """
    db = AsyncDatabaseManager("sqlite+aiosqlite:///:memory:")
    await db.connect()
    await db.create_tables()

    async with db.get_session_context() as session:
        user = TzUser(email="root@example.com", hashed_password="", is_admin=True)
        user.set_password("hunter2")
        session.add(user)
        row = ScheduledRun(label="Kickoff", runs_at=STORED)
        session.add(row)
        await session.commit()
        await session.refresh(row)

    site = AdminSite(title="TZ Admin")
    site.register(run_admin)

    app = FastAPI()
    app.include_router(
        make_admin_router(
            site,
            db=db,
            auth_backend=UserModelAuthBackend(TzUser),
            secret_key=SECRET,
            cookie_secure=False,
        ),
    )
    yield app, db, row
    await db.drop_tables()
    await db.disconnect()


@asynccontextmanager
async def _signed_in(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Yield a client that already holds the admin session cookie.

    Args:
        app (FastAPI): The app serving the panel.

    Yields:
        AsyncClient: A logged-in client.
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        await client.post(
            "/admin/login",
            data={"identifier": "root@example.com", "password": "hunter2"},
            follow_redirects=False,
        )
        yield client


class TestTheRenderedPanel:
    """The three views an operator reads must agree with each other."""

    @pytest.mark.asyncio
    async def test_the_list_prints_the_local_hour(
        self,
        panel: tuple[FastAPI, AsyncDatabaseManager, ScheduledRun],
    ) -> None:
        """A list in UTC beside a form in local time is two answers."""
        app, _db, _row = panel
        async with _signed_in(app) as client:
            response = await client.get("/admin/m/admin_tz_run/")

        assert response.status_code == 200
        assert "2026-06-15 20:00:00-03:00" in response.text

    @pytest.mark.asyncio
    async def test_the_edit_form_prefills_the_local_hour(
        self,
        panel: tuple[FastAPI, AsyncDatabaseManager, ScheduledRun],
    ) -> None:
        """And says which zone the box means, since the input cannot."""
        app, _db, row = panel
        async with _signed_in(app) as client:
            response = await client.get(f"/admin/m/admin_tz_run/{row.id}/edit")

        assert response.status_code == 200
        assert 'value="2026-06-15T20:00"' in response.text
        assert SAO_PAULO in response.text

    @pytest.mark.asyncio
    async def test_saving_the_form_keeps_the_instant(
        self,
        panel: tuple[FastAPI, AsyncDatabaseManager, ScheduledRun],
    ) -> None:
        """Re-submitting an untouched form must not move the row.

        The half-conversion (convert on write only) passes every unit
        test about writing and still drifts three hours on each save,
        which is why this one goes through the real POST.
        """
        app, db, row = panel
        async with _signed_in(app) as client:
            page = await client.get(f"/admin/m/admin_tz_run/{row.id}/edit")
            token = page.text.split('name="csrf_token" value="')[1].split('"')[0]
            saved = await client.post(
                f"/admin/m/admin_tz_run/{row.id}/edit",
                data={
                    "csrf_token": token,
                    "label": "Kickoff",
                    "runs_at": "2026-06-15T20:00",
                    "is_active": "true",
                },
                follow_redirects=False,
            )

        assert saved.status_code == 303
        async with db.get_session_context() as session:
            stored = await session.get(ScheduledRun, row.id)
            assert stored is not None
            assert to_display_timezone(stored.runs_at, dt.UTC) == STORED


class TestTheSiteDefault:
    """The zone belongs to the panel, so the site can declare it once."""

    def test_a_registered_model_inherits_the_site_zone(self) -> None:
        """Otherwise every registration repeats the same string."""
        site = AdminSite(title="TZ", display_timezone=SAO_PAULO)
        admin: AdminModel[Meeting] = AdminModel(model=Meeting)

        site.register(admin)

        assert admin.display_timezone == SAO_PAULO
        assert admin.display_tzinfo == ZoneInfo(SAO_PAULO)

    def test_a_model_keeps_its_own_zone(self) -> None:
        """A table read by someone elsewhere overrides the default."""
        site = AdminSite(title="TZ", display_timezone=SAO_PAULO)
        admin: AdminModel[Meeting] = AdminModel(
            model=Meeting,
            display_timezone="UTC",
        )

        site.register(admin)

        assert admin.display_timezone == "UTC"

    def test_an_unknown_site_zone_fails_at_declaration(self) -> None:
        """Same guard as the model's, at the other place it is typed."""
        with pytest.raises(ValueError, match="not a zone"):
            AdminSite(title="TZ", display_timezone="Mars/Olympus")
