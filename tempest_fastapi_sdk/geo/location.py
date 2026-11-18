"""Where a row is in Brazil: its city and state, checked against the IBGE.

* :class:`BRLocationMixin` — ``city``, ``state`` and ``city_ibge_code``
  columns for a model (a user, a store), filled by
  :meth:`~BRLocationMixin.set_br_location`, which resolves the city in the
  municipalities the SDK ships and refuses one the state does not have.
* :class:`BRLocationSchema` — the same check at the API edge: an unknown
  city answers ``422``, a known one comes back with the official spelling.
* :func:`br_location_distribution` — how many rows per state and per city,
  in two ``GROUP BY`` queries, for a map
  (:func:`~tempest_fastapi_sdk.geo.render_br_map_svg`) or a report.

Brazil only: the state is one of the 27 federative units and the city one
of the 5571 municipalities. Nothing here makes a request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from pydantic import Field, model_validator
from sqlalchemy import Integer, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from tempest_fastapi_sdk.geo.municipalities import (
    Municipality,
    municipality_by_ibge_code,
    municipality_by_name,
)
from tempest_fastapi_sdk.schemas import BaseSchema
from tempest_fastapi_sdk.utils.locations import UFEnum

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import ColumnElement

BR_CITY_MAX_LENGTH: int = 64
"""Width of the ``city`` column; the longest IBGE name has 32 characters."""


class UnknownMunicipalityError(ValueError):
    """The state has no municipality by that name.

    Attributes:
        city (str): The name that was looked up.
        state (str): The state it was looked up in.
    """

    def __init__(self, city: str, state: str) -> None:
        """Build the error.

        Args:
            city (str): The name that was looked up.
            state (str): The state it was looked up in.
        """
        super().__init__(f"{state} has no municipality named {city!r}")
        self.city: str = city
        self.state: str = state


def resolve_br_municipality(
    city: str, state: UFEnum | str, *, typo_cutoff: float | None = None
) -> Municipality:
    """Return the municipality a city name and a state point at.

    Case, accents and punctuation do not matter (``"sao paulo"``,
    ``"SÃO PAULO"``), and older IBGE spellings count.

    Args:
        city (str): The city name.
        state (UFEnum | str): The state, as enum or acronym.
        typo_cutoff (float | None): Accept the most similar name in the
            state at this similarity or above when no name matches exactly
            (see :func:`~tempest_fastapi_sdk.geo.municipality_by_name`).
            ``None`` (default) is exact only.

    Returns:
        Municipality: The municipality.

    Raises:
        UnknownMunicipalityError: When the state has no such municipality,
            or ``state`` is not a Brazilian state.
    """
    found = municipality_by_name(city, state, typo_cutoff=typo_cutoff)
    if found is None:
        raise UnknownMunicipalityError(city, str(state))
    return found


class BRLocationMixin:
    """Mixin adding a Brazilian city and state to a model.

    Mix it into a concrete model alongside
    :class:`~tempest_fastapi_sdk.BaseModel` (or ``BaseUserModel``)::

        class UserModel(BRLocationMixin, BaseUserModel):
            __tablename__ = "users"

    The three columns are nullable, so an existing table gains them with a
    plain migration and old rows stay valid until they are filled. ``state``
    and ``city_ibge_code`` are indexed: they are what
    :func:`br_location_distribution` groups by.

    Write through :meth:`set_br_location`, which stores the official
    spelling and the IBGE code; assigning the columns by hand skips the
    check and leaves the row off the city map when the code is missing.

    Attributes:
        city (str | None): The official IBGE name of the city.
        state (str | None): The state's two-letter acronym (``"SP"``).
        city_ibge_code (int | None): The city's seven-digit IBGE code.
    """

    city: Mapped[str | None] = mapped_column(
        String(BR_CITY_MAX_LENGTH),
        nullable=True,
        doc="Official IBGE name of the city.",
    )
    state: Mapped[str | None] = mapped_column(
        String(2),
        nullable=True,
        index=True,
        doc="Two-letter acronym of the Brazilian state.",
    )
    city_ibge_code: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        index=True,
        doc="Seven-digit IBGE code of the city.",
    )

    def set_br_location(
        self, city: str, state: UFEnum | str, *, typo_cutoff: float | None = None
    ) -> Municipality:
        """Set the city and state, checked against the IBGE municipalities.

        Args:
            city (str): The city name, in any case and with or without
                accents.
            state (UFEnum | str): The state, as enum or acronym.
            typo_cutoff (float | None): Accept a near spelling at this
                similarity or above (see :func:`resolve_br_municipality`).

        Returns:
            Municipality: The municipality stored.

        Raises:
            UnknownMunicipalityError: When the state has no such
                municipality; the row is left unchanged.
        """
        municipality = resolve_br_municipality(city, state, typo_cutoff=typo_cutoff)
        self.city = municipality.name
        self.state = municipality.uf.value
        self.city_ibge_code = municipality.ibge_code
        return municipality

    def clear_br_location(self) -> None:
        """Forget the city and state."""
        self.city = None
        self.state = None
        self.city_ibge_code = None

    def br_municipality(self) -> Municipality | None:
        """Return the stored city's municipality record.

        Returns:
            Municipality | None: The record, or ``None`` when no IBGE code
            is stored.
        """
        if self.city_ibge_code is None:
            return None
        return municipality_by_ibge_code(self.city_ibge_code)


class BRLocationSchema(BaseSchema):
    """A Brazilian city and state, checked at the API edge.

    The city must exist in the state; an unknown one fails validation, so a
    FastAPI route answers ``422`` before the handler runs. On success
    ``city`` holds the official spelling and ``city_ibge_code`` is filled.

    Attributes:
        city (str): The city; replaced by its official IBGE name.
        state (UFEnum): The state.
        city_ibge_code (int | None): Filled by validation; any value sent is
            overwritten.
    """

    city: str = Field(min_length=1, max_length=BR_CITY_MAX_LENGTH)
    state: UFEnum
    city_ibge_code: int | None = None

    @model_validator(mode="after")
    def _resolve(self) -> Self:
        """Resolve the city in the state and store its official form.

        Returns:
            Self: The schema, with ``city`` and ``city_ibge_code`` set.

        Raises:
            ValueError: When the state has no municipality by that name.
        """
        municipality = resolve_br_municipality(self.city, self.state)
        object.__setattr__(self, "city", municipality.name)
        object.__setattr__(self, "city_ibge_code", municipality.ibge_code)
        return self


class BRStateCountSchema(BaseSchema):
    """How many rows one state has.

    Attributes:
        state (str): The state's acronym.
        count (int): Rows with that state.
    """

    state: str
    count: int


class BRCityCountSchema(BaseSchema):
    """How many rows one city has.

    Attributes:
        city_ibge_code (int): The city's IBGE code.
        city (str): Its official name.
        state (str): Its state's acronym.
        latitude (float): Its town centre.
        longitude (float): Its town centre.
        count (int): Rows with that city.
    """

    city_ibge_code: int
    city: str
    state: str
    latitude: float
    longitude: float
    count: int


class BRLocationDistributionSchema(BaseSchema):
    """Rows of a model per state and per city.

    Attributes:
        total (int): Rows counted.
        located (int): Rows with a state.
        unlocated (int): Rows with no state.
        states (list[BRStateCountSchema]): One entry per state with rows,
            largest first.
        cities (list[BRCityCountSchema]): One entry per city with rows,
            largest first; a row with a state and no IBGE code counts in
            ``states`` only.
    """

    total: int
    located: int
    unlocated: int
    states: list[BRStateCountSchema] = Field(default_factory=list)
    cities: list[BRCityCountSchema] = Field(default_factory=list)

    def state_counts(self) -> dict[str, int]:
        """Return the count of each state, for the map.

        Returns:
            dict[str, int]: ``{"SP": 840, ...}``.
        """
        return {entry.state: entry.count for entry in self.states}


async def br_location_distribution(
    session: AsyncSession,
    model: type[BRLocationMixin],
    *,
    where: ColumnElement[bool] | None = None,
    city_limit: int | None = None,
) -> BRLocationDistributionSchema:
    """Count a model's rows per state and per city.

    Three aggregate queries — the total, a ``GROUP BY state`` and a
    ``GROUP BY city_ibge_code`` — so the cost does not grow with the number
    of rows loaded into Python. A code the shipped municipalities do not
    know (a row written by hand) is left out of ``cities``.

    Args:
        session (AsyncSession): The session to query on.
        model (type[BRLocationMixin]): A model with the mixin's columns.
        where (ColumnElement[bool] | None): An extra filter
            (``UserModel.is_active.is_(True)``).
        city_limit (int | None): Keep only the largest cities; ``None``
            keeps all.

    Returns:
        BRLocationDistributionSchema: The counts.

    Raises:
        TypeError: When ``model`` does not have the mixin's columns.
    """
    columns: Any = model
    for name in ("state", "city_ibge_code"):
        if not hasattr(columns, name):
            raise TypeError(f"{model.__name__} has no {name!r}; mix BRLocationMixin in")
    total_query = select(func.count()).select_from(columns)
    state_query = (
        select(columns.state, func.count())
        .where(columns.state.is_not(None))
        .group_by(columns.state)
    )
    city_query = (
        select(columns.city_ibge_code, func.count())
        .where(columns.city_ibge_code.is_not(None))
        .group_by(columns.city_ibge_code)
    )
    if where is not None:
        total_query = total_query.where(where)
        state_query = state_query.where(where)
        city_query = city_query.where(where)
    total = int((await session.execute(total_query)).scalar_one())
    states = sorted(
        (
            BRStateCountSchema(state=str(state), count=int(count))
            for state, count in (await session.execute(state_query)).all()
        ),
        key=lambda entry: (-entry.count, entry.state),
    )
    cities: list[BRCityCountSchema] = []
    for code, count in (await session.execute(city_query)).all():
        municipality = municipality_by_ibge_code(int(code))
        if municipality is None:
            continue
        cities.append(
            BRCityCountSchema(
                city_ibge_code=municipality.ibge_code,
                city=municipality.name,
                state=municipality.uf.value,
                latitude=municipality.coordinate.latitude,
                longitude=municipality.coordinate.longitude,
                count=int(count),
            )
        )
    cities.sort(key=lambda entry: (-entry.count, entry.city))
    if city_limit is not None:
        cities = cities[:city_limit]
    located = sum(entry.count for entry in states)
    return BRLocationDistributionSchema(
        total=total,
        located=located,
        unlocated=total - located,
        states=states,
        cities=cities,
    )


__all__: list[str] = [
    "BR_CITY_MAX_LENGTH",
    "BRCityCountSchema",
    "BRLocationDistributionSchema",
    "BRLocationMixin",
    "BRLocationSchema",
    "BRStateCountSchema",
    "UnknownMunicipalityError",
    "br_location_distribution",
    "resolve_br_municipality",
]
