"""A serializer as a tree of fields, for the documentation site.

``tree(Serializer, "request")`` gives the fields a caller sends;
``tree(Serializer, "response")`` the fields they receive. Nested objects and
lists of objects carry ``children`` - the site opens them with the plus icon.
Each node: name, type, required, description (the field's ``help_text``),
allowed values and details (limits, defaults, formats).
"""

from rest_framework import serializers as s


def _type(field):
    """A plain type label, most specific first (EmailField is a CharField)."""
    if isinstance(field, s.ListSerializer):
        return "array of objects"
    if isinstance(field, s.BaseSerializer):
        return "object"
    if isinstance(field, s.ListField):
        child = field.child
        return f"array of {_type(child)}" if child is not None else "array"
    if isinstance(field, s.DictField):
        return "object"
    if isinstance(field, s.JSONField):
        return "any JSON"
    if isinstance(field, s.ImageField):
        return "file (image)"
    if isinstance(field, s.FileField):
        return "file"
    if isinstance(field, s.BooleanField):
        return "boolean"
    if isinstance(field, s.IntegerField):
        return "integer"
    if isinstance(field, s.FloatField):
        return "number"
    if isinstance(field, s.DecimalField):
        return "string (decimal)"
    if isinstance(field, s.DateTimeField):
        return "string (date-time)"
    if isinstance(field, s.DateField):
        return "string (date)"
    if isinstance(field, s.TimeField):
        return "string (time)"
    if isinstance(field, s.UUIDField):
        return "string (uuid)"
    if isinstance(field, s.EmailField):
        return "string (email)"
    if isinstance(field, s.URLField):
        return "string (url)"
    if isinstance(field, s.MultipleChoiceField):
        return "array of strings"
    if isinstance(field, s.ChoiceField):
        return "string"
    if isinstance(field, s.SerializerMethodField):
        return getattr(field, "doc_type", "string")
    return "string"


def _details(field):
    found = []
    for attribute, words in (("min_length", "at least {} characters"),
                             ("max_length", "at most {} characters"),
                             ("min_value", "at least {}"), ("max_value", "at most {}")):
        value = getattr(field, attribute, None)
        if value is not None:
            found.append(words.format(value))
    if isinstance(field, s.DecimalField):
        found.append(f"up to {field.decimal_places} decimal places")
    if getattr(field, "allow_null", False):
        found.append("may be null")
    default = getattr(field, "default", s.empty)
    if default is not s.empty and not callable(default):
        found.append(f"default {default!r}")
    return found


def _choices(field):
    if isinstance(field, (s.ChoiceField, s.MultipleChoiceField)):
        return [{"value": str(key), "label": str(label)} for key, label in field.choices.items()]
    return []


def _wanted(field, mode):
    if mode == "request":
        return not field.read_only
    return not field.write_only


def _node(name, field, mode):
    node = {
        "name": name,
        "type": _type(field),
        "required": bool(field.required) if mode == "request" else False,
        "description": str(field.help_text or ""),
        "choices": _choices(field),
        "details": _details(field),
        "children": [],
    }
    inner = None
    if isinstance(field, s.ListSerializer):
        inner = field.child
    elif isinstance(field, s.BaseSerializer):
        inner = field
    elif isinstance(field, s.ListField) and isinstance(field.child, s.BaseSerializer):
        inner = field.child
    if inner is not None:
        node["children"] = tree(inner, mode)
    return node


def tree(serializer, mode):
    """The fields of a serializer class or instance, for ``mode``."""
    if serializer is None:
        return []
    instance = serializer() if isinstance(serializer, type) else serializer
    return [_node(name, field, mode) for name, field in instance.fields.items()
            if _wanted(field, mode)]


def walk(nodes, path=""):
    """Every node with its dotted path - for the documentation test."""
    for node in nodes:
        here = f"{path}.{node['name']}" if path else node["name"]
        yield here, node
        yield from walk(node["children"], here)


def count(nodes):
    return sum(1 for _ in walk(nodes))
