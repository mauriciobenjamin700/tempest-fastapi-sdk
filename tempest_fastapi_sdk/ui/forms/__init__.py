"""Forms generated from Pydantic schemas, and parsed back into them.

The schema that already validates the request also describes the form:
field names, types, defaults, constraints, titles and descriptions. This
package renders that description as accessible HTML and reads the
submission back, so the form and the model can never drift apart.

The round trip:

1. :func:`form_for` renders a ``<form>`` for the schema — labels,
   controls, native validation attributes, hints.
2. :func:`parse_form` reads the POST, coerces what HTML cannot express
   (unchecked checkboxes, empty optionals, multi-selects) and validates.
3. On failure, the returned :class:`FormResult` carries per-field
   messages and the raw input, which go straight back into
   :func:`form_for` to re-render the page with nothing lost.

Between steps 1 and 2, :func:`form_spec_for` and :func:`fields_for`
expose the generated form as plain data — patch a label, drop a field,
reorder — before :func:`render_form` turns it into widgets.

Example:
    ```python
    from fastapi import FastAPI, Request
    from fastapi.responses import RedirectResponse, Response
    from pydantic import BaseModel, EmailStr, Field

    from tempest_fastapi_sdk.ssr import html_response
    from tempest_fastapi_sdk.ui.forms import form_for, parse_form

    app: FastAPI = FastAPI()


    class SignupSchema(BaseModel):
        email: EmailStr
        password: str = Field(min_length=8)


    @app.get("/signup")
    async def signup_form() -> Response:
        return html_response(
            form_for(SignupSchema, action="/signup"),
            title="Cadastro",
        )


    @app.post("/signup")
    async def signup(request: Request) -> Response:
        result = await parse_form(SignupSchema, request)
        if not result.ok:
            return html_response(
                form_for(
                    SignupSchema,
                    action="/signup",
                    values=result.values,
                    errors=result.errors,
                ),
                title="Cadastro",
                status_code=422,
            )
        return RedirectResponse("/", status_code=303)
    ```
"""

from tempest_fastapi_sdk.ui.forms.introspect import (
    UnsupportedFieldError as UnsupportedFieldError,
)
from tempest_fastapi_sdk.ui.forms.introspect import fields_for as fields_for
from tempest_fastapi_sdk.ui.forms.introspect import form_spec_for as form_spec_for
from tempest_fastapi_sdk.ui.forms.parse import FormResult as FormResult
from tempest_fastapi_sdk.ui.forms.parse import parse_form as parse_form
from tempest_fastapi_sdk.ui.forms.render import form_for as form_for
from tempest_fastapi_sdk.ui.forms.render import render_field as render_field
from tempest_fastapi_sdk.ui.forms.render import render_form as render_form
from tempest_fastapi_sdk.ui.forms.spec import Control as Control
from tempest_fastapi_sdk.ui.forms.spec import FieldSpec as FieldSpec
from tempest_fastapi_sdk.ui.forms.spec import FormClasses as FormClasses
from tempest_fastapi_sdk.ui.forms.spec import FormSpec as FormSpec
from tempest_fastapi_sdk.ui.forms.spec import SelectOption as SelectOption
from tempest_fastapi_sdk.ui.forms.styles import form_stylesheet as form_stylesheet

__all__: list[str] = [
    "Control",
    "FieldSpec",
    "FormClasses",
    "FormResult",
    "FormSpec",
    "SelectOption",
    "UnsupportedFieldError",
    "fields_for",
    "form_for",
    "form_spec_for",
    "form_stylesheet",
    "parse_form",
    "render_field",
    "render_form",
]
