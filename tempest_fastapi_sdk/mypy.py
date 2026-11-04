"""mypy plugin: an enum field of a ``use_enum_values`` model reads as its value.

``use_enum_values=True`` — set by :class:`~tempest_fastapi_sdk.BaseSchema` — makes
Pydantic store the enum's **value** in a field annotated with the enum. The
annotation still says the enum, so mypy accepts code the runtime refuses.
Measured on ``mypy --strict`` with ``status: StatusEnum`` on a ``BaseSchema``:
``order.status.value``, ``order.status.name`` and passing ``order.status`` to a
function that expects ``StatusEnum`` all type-check and all raise
``AttributeError``.

With this plugin, reading such a field yields the type of the value instead:
``str`` for a ``str``-based enum (:class:`~tempest_fastapi_sdk.BaseStrEnum`,
``StrEnum``), ``int`` for an ``int``-based one, and ``object`` for a plain
``Enum``, whose values can be anything. ``Optional``, unions, ``list``,
``tuple``, ``dict`` and the other generics are rewritten argument by
argument. Construction is unchanged: the generated ``__init__`` still accepts
the member, and also takes the value: a field read from one
``use_enum_values`` model passed to another one's constructor is a ``str``,
which Pydantic validates back into the enum. Without that, the plugin flagged
seven such calls inside this package, every one of them correct at runtime.

Enable it next to Pydantic's own plugin::

    [tool.mypy]
    plugins = ["pydantic.mypy", "tempest_fastapi_sdk.mypy"]

How it decides:

* At semantic analysis (mypy's MRO hook, which runs once per class with its
  body at hand), every class records whether it stores enum values:
  its own ``model_config = ConfigDict(use_enum_values=...)`` (or a ``dict``
  literal) wins, otherwise the nearest base that recorded it. The result lives
  in the class's mypy metadata, so it survives the incremental cache and
  reaches classes defined in installed packages.
* At each attribute read, a field declared in the class body of such a model
  has its enum parts replaced by the value types above. Methods, properties
  and ``ClassVar`` are left alone.

mypy keeps only the **first** plugin that answers a given hook. The MRO hook
is one neither ``pydantic.mypy`` nor mypy's default plugin claims, and the
attribute hook answers only for a field that qualifies, so the order of the
two plugins in ``plugins = [...]`` does not matter and neither shadows the
other.

Only mypy runs plugins. Pyright, Pylance and basedpyright keep reading the
annotation as written.

A constructor called through a subscripted generic (``Model[T](...)``) is not
widened: the callee of that call has no name, and mypy offers a plugin no hook
for it. Convert there explicitly (``StatusEnum(value)``), which Pydantic
accepts as well.
"""

from __future__ import annotations

from collections.abc import Callable

from mypy.nodes import (
    AssignmentStmt,
    CallExpr,
    DictExpr,
    Expression,
    NameExpr,
    StrExpr,
    TypeInfo,
    Var,
)
from mypy.plugin import AttributeContext, ClassDefContext, FunctionSigContext, Plugin
from mypy.types import (
    FunctionLike,
    Instance,
    LiteralType,
    ProperType,
    TupleType,
    Type,
    UnionType,
    get_proper_type,
)

METADATA_KEY: str = "tempest_fastapi_sdk.enum_values"
"""Key under ``TypeInfo.metadata`` where each class records the flag."""

_CONFIG_NAME: str = "model_config"
_CONFIG_KEY: str = "use_enum_values"


def _literal_flag(expression: Expression) -> bool | None:
    """Read ``True`` / ``False`` from an expression, if it is that literal.

    Args:
        expression (Expression): The keyword or dict value.

    Returns:
        bool | None: The literal, or ``None`` for anything else.
    """
    if isinstance(expression, NameExpr) and expression.fullname in {
        "builtins.True",
        "builtins.False",
    }:
        return expression.fullname == "builtins.True"
    if isinstance(expression, NameExpr) and expression.name in {"True", "False"}:
        return expression.name == "True"
    return None


def _declared_flag(info: TypeInfo) -> bool | None:
    """Read ``use_enum_values`` from the class body's ``model_config``.

    Args:
        info (TypeInfo): The class being analyzed, with its body.

    Returns:
        bool | None: The declared value, or ``None`` when the body does not
        declare it.
    """
    for statement in info.defn.defs.body:
        if not isinstance(statement, AssignmentStmt):
            continue
        targets = [
            lvalue for lvalue in statement.lvalues if isinstance(lvalue, NameExpr)
        ]
        if not any(target.name == _CONFIG_NAME for target in targets):
            continue
        value = statement.rvalue
        if isinstance(value, CallExpr):
            for name, argument in zip(value.arg_names, value.args, strict=False):
                if name == _CONFIG_KEY:
                    return _literal_flag(argument)
        if isinstance(value, DictExpr):
            for key, item in value.items:
                if isinstance(key, StrExpr) and key.value == _CONFIG_KEY:
                    return _literal_flag(item)
    return None


def _inherited_flag(info: TypeInfo) -> bool:
    """Return the flag recorded by the nearest base class.

    Args:
        info (TypeInfo): The class whose bases to read.

    Returns:
        bool: The nearest recorded flag, ``False`` when no base recorded one.
    """
    for base in info.mro[1:]:
        recorded = base.metadata.get(METADATA_KEY)
        if recorded is not None:
            return bool(recorded.get(_CONFIG_KEY, False))
    return False


def _record(ctx: ClassDefContext) -> None:
    """Record on the class whether its fields store enum values.

    Args:
        ctx (ClassDefContext): The class being analyzed.
    """
    info = ctx.cls.info
    declared = _declared_flag(info)
    flag = _inherited_flag(info) if declared is None else declared
    info.metadata[METADATA_KEY] = {_CONFIG_KEY: flag}


def _stores_values(info: TypeInfo) -> bool:
    """Tell whether instances of ``info`` hold enum values in their fields.

    Args:
        info (TypeInfo): The class of the object being read.

    Returns:
        bool: The flag recorded on the class or its nearest base.
    """
    for klass in info.mro:
        recorded = klass.metadata.get(METADATA_KEY)
        if recorded is not None:
            return bool(recorded.get(_CONFIG_KEY, False))
    return False


def _owner_field(info: TypeInfo, name: str) -> bool:
    """Tell whether ``name`` is a field declared in a class body of the MRO.

    Args:
        info (TypeInfo): The class of the object being read.
        name (str): The attribute name.

    Returns:
        bool: ``True`` for an instance variable declared in a class body;
        ``False`` for a method, a property or a ``ClassVar``.
    """
    for klass in info.mro:
        symbol = klass.names.get(name)
        if symbol is None:
            continue
        node = symbol.node
        return (
            isinstance(node, Var)
            and not node.is_classvar
            and not node.is_property
            and not node.is_classmethod
            and not node.is_staticmethod
        )
    return False


def _mentions_enum(declared: Type) -> bool:
    """Tell whether an enum appears anywhere in ``declared``.

    Args:
        declared (Type): A field's declared type.

    Returns:
        bool: ``True`` when some part of it is an enum or an enum literal.
    """
    proper = get_proper_type(declared)
    if isinstance(proper, LiteralType):
        return proper.fallback.type.is_enum
    if isinstance(proper, Instance):
        return proper.type.is_enum or any(_mentions_enum(arg) for arg in proper.args)
    if isinstance(proper, UnionType):
        return any(_mentions_enum(item) for item in proper.items)
    if isinstance(proper, TupleType):
        return any(_mentions_enum(item) for item in proper.items)
    return False


class _ValueTypes:
    """Rewrite enum types to the type of their values, recursively.

    Attributes:
        named_type (Callable[[str, list[Type]], Instance]): Builds a builtin
            instance, from the checker API.
        changed (bool): Whether any part was rewritten.
    """

    def __init__(self, named_type: Callable[[str, list[Type]], Instance]) -> None:
        """Bind the type builder.

        Args:
            named_type (Callable[[str, list[Type]], Instance]): The API's
                ``named_generic_type``.
        """
        self.named_type = named_type
        self.changed: bool = False

    def _value_of(self, info: TypeInfo) -> Instance:
        """Return the value type of the enum ``info``.

        Args:
            info (TypeInfo): An enum class.

        Returns:
            Instance: ``str`` or ``int`` for a mixed-in enum, else ``object``.
        """
        self.changed = True
        bases = {klass.fullname for klass in info.mro}
        if "builtins.str" in bases:
            return self.named_type("builtins.str", [])
        if "builtins.int" in bases:
            return self.named_type("builtins.int", [])
        return self.named_type("builtins.object", [])

    def rewrite(self, original: Type) -> Type:
        """Return ``original`` with every enum part replaced by its value type.

        Args:
            original (Type): A field's declared type.

        Returns:
            Type: The rewritten type; ``original`` when nothing was an enum.
        """
        proper: ProperType = get_proper_type(original)
        if isinstance(proper, LiteralType) and proper.fallback.type.is_enum:
            return self._value_of(proper.fallback.type)
        if isinstance(proper, Instance):
            if proper.type.is_enum:
                return self._value_of(proper.type)
            if proper.args:
                return proper.copy_modified(
                    args=[self.rewrite(arg) for arg in proper.args]
                )
            return original
        if isinstance(proper, UnionType):
            return UnionType.make_union(
                [self.rewrite(item) for item in proper.items],
                proper.line,
                proper.column,
            )
        if isinstance(proper, TupleType):
            return proper.copy_modified(
                items=[self.rewrite(item) for item in proper.items]
            )
        return original


def _read_as_value(ctx: AttributeContext) -> Type:
    """Type a field read on a ``use_enum_values`` model as the enum's value.

    Args:
        ctx (AttributeContext): The attribute access.

    Returns:
        Type: The rewritten type, or the default one when the object, the
        attribute or its type does not qualify.
    """
    owner = get_proper_type(ctx.type)
    if not isinstance(owner, Instance) or not _stores_values(owner.type):
        return ctx.default_attr_type
    name = getattr(ctx.context, "name", None)
    if not isinstance(name, str) or not _owner_field(owner.type, name):
        return ctx.default_attr_type
    rewriter = _ValueTypes(ctx.api.named_generic_type)
    rewritten = rewriter.rewrite(ctx.default_attr_type)
    return rewritten if rewriter.changed else ctx.default_attr_type


def _accept_values(ctx: FunctionSigContext) -> FunctionLike:
    """Let a ``use_enum_values`` model's constructor take members or values.

    Args:
        ctx (FunctionSigContext): The constructor call.

    Returns:
        FunctionLike: The signature with each enum-typed parameter widened to
        ``declared | value type``.
    """
    signature = ctx.default_signature
    widened: list[Type] = []
    for declared in signature.arg_types:
        rewriter = _ValueTypes(ctx.api.named_generic_type)
        rewritten = rewriter.rewrite(declared)
        widened.append(
            UnionType.make_union([declared, rewritten])
            if rewriter.changed
            else declared
        )
    return signature.copy_modified(arg_types=widened)


class EnumValuesPlugin(Plugin):
    """Make mypy read enum fields of ``use_enum_values`` models as values."""

    def get_customize_class_mro_hook(
        self, fullname: str
    ) -> Callable[[ClassDefContext], None]:
        """Record the flag on every class, once its MRO is known.

        Args:
            fullname (str): The class being analyzed.

        Returns:
            Callable[[ClassDefContext], None]: The recorder.
        """
        return _record

    def _qualifying_model(self, fullname: str) -> TypeInfo | None:
        """Look up a class that stores enum values.

        Args:
            fullname (str): The class's full name.

        Returns:
            TypeInfo | None: The class, or ``None`` when it is unknown or
            does not store enum values.
        """
        symbol = self.lookup_fully_qualified(fullname)
        info = symbol.node if symbol is not None else None
        if not isinstance(info, TypeInfo) or not _stores_values(info):
            return None
        return info

    def get_function_signature_hook(
        self, fullname: str
    ) -> Callable[[FunctionSigContext], FunctionLike] | None:
        """Claim the constructor call of a ``use_enum_values`` model.

        Args:
            fullname (str): The callee; a class name for a constructor call.

        Returns:
            Callable[[FunctionSigContext], FunctionLike] | None: The widener
            for a qualifying model; ``None`` otherwise, so the call reaches
            the next plugin in the chain.
        """
        if self._qualifying_model(fullname) is None:
            return None
        return _accept_values

    def get_attribute_hook(
        self, fullname: str
    ) -> Callable[[AttributeContext], Type] | None:
        """Claim the read of an enum field of a ``use_enum_values`` model.

        Args:
            fullname (str): ``module.Class.attribute`` of the attribute read.

        Returns:
            Callable[[AttributeContext], Type] | None: The rewriter for a
            qualifying field; ``None`` otherwise, so the read reaches the
            next plugin in the chain.
        """
        owner_name, _, attribute = fullname.rpartition(".")
        if not owner_name:
            return None
        info = self._qualifying_model(owner_name)
        if info is None:
            return None
        own = info.names.get(attribute)
        node = own.node if own is not None else None
        if not isinstance(node, Var) or node.type is None:
            return None
        if not _owner_field(info, attribute) or not _mentions_enum(node.type):
            return None
        return _read_as_value


def plugin(version: str) -> type[Plugin]:
    """Entry point mypy calls with its own version.

    Args:
        version (str): The running mypy version.

    Returns:
        type[Plugin]: :class:`EnumValuesPlugin`.
    """
    return EnumValuesPlugin


__all__: list[str] = ["METADATA_KEY", "EnumValuesPlugin", "plugin"]
