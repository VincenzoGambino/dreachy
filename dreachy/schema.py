"""Content-model discovery: which node types a site has, and which of their
fields hold speakable text.

Pure functions over JSON:API data, no HTTP: JsonApiBackend.get_schema()
fetches the evidence, this module decides.

Discovery route (chosen 2026-09-30 against Drupal core 11.4.4):

- Bundle list: the JSON:API index (``/jsonapi``). Always readable
  anonymously, and lists exactly the resource types JSON:API exposes.
- Human labels: ``node_type--node_type``, best effort. Core 11.4 lets anyone
  with ``access content`` view node types; older cores and locked-down sites
  don't, so a missing label is derived from the machine name.
- Text fields: inferred from sample nodes. Field definitions
  (``field_config--field_config``) need ``administer node fields``, which
  anonymous never has, so the shape of real attribute values is the only
  anonymous signal: formatted text is an object with ``value`` plus
  ``format`` or ``processed``.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Every node type's label field is its title; kept per type so the client
# never hard-codes it.
LABEL_FIELD = "title"

_BODY_PREFERENCE = ("body", "field_body")
_SUMMARY_HINTS = ("summary", "teaser")


@dataclass(frozen=True)
class TypeSchema:
    label: str
    label_field: str
    # Body candidates, best first: the first non-empty one is read aloud.
    text_fields: tuple[str, ...]
    # A teaser that's a field of its own (Umami's recipe), if the type has one.
    summary_field: str | None = None


Schema = dict[str, TypeSchema]

# The Umami demo profile, confirmed against the live site 2026-07-27. Used
# when discovery fails outright, and as the best guess for an Umami type
# with no content yet to infer from.
FALLBACK_TYPES: Schema = {
    "article": TypeSchema("Article", LABEL_FIELD, ("field_body",)),
    "page": TypeSchema("Basic page", LABEL_FIELD, ("field_body",)),
    "recipe": TypeSchema("Recipe", LABEL_FIELD, ("field_recipe_instruction",), "field_summary"),
}


def humanize(machine_name: str) -> str:
    """``news_item`` -> ``News item``: a label for a type the site didn't name."""
    return machine_name.replace("_", " ").capitalize()


def _is_formatted_text(value: Any) -> bool:
    return isinstance(value, dict) and "value" in value and ("format" in value or "processed" in value)


def infer_type_schema(label: str, samples: Iterable[Mapping[str, Any]]) -> TypeSchema | None:
    """Pick a type's text fields from sample nodes' attributes.

    Samples are unioned, so a field left empty on the newest node is still
    found on an older one. Returns None for a type with no formatted text.
    """
    candidates: list[str] = []
    for attributes in samples:
        for name, value in attributes.items():
            if name not in candidates and _is_formatted_text(value):
                candidates.append(name)

    summary = next((name for name in candidates if any(hint in name for hint in _SUMMARY_HINTS)), None)
    body = [name for name in candidates if name != summary]
    if not body:
        if summary is None:
            return None
        # A lone teaser-named field is still the only text the type has.
        body, summary = [summary], None
    preferred = [name for name in _BODY_PREFERENCE if name in body]
    rest = [name for name in body if name not in preferred]
    return TypeSchema(label, LABEL_FIELD, tuple(preferred + rest), summary)


def guess_type_schema(bundle: str, label: str) -> TypeSchema:
    """A type with nothing to infer from: its Umami mapping if it has one,
    plus the common body field names either way."""
    known = FALLBACK_TYPES.get(bundle)
    known_fields = known.text_fields if known else ()
    fields = tuple(dict.fromkeys((*known_fields, *_BODY_PREFERENCE)))
    return TypeSchema(label, LABEL_FIELD, fields, known.summary_field if known else None)


def build_schema(
    bundles: Iterable[str],
    labels: Mapping[str, str],
    samples: Mapping[str, list[Mapping[str, Any]] | None],
) -> Schema:
    """Assemble the site's schema, ordered by machine name.

    ``samples[bundle]`` is None when the bundle couldn't be read (skipped),
    and empty when it has no content yet (kept with guessed fields, so the
    watcher still notices its first node).
    """
    schema: Schema = {}
    for bundle in sorted(bundles):
        bundle_samples = samples.get(bundle)
        if bundle_samples is None:
            continue
        label = labels.get(bundle) or humanize(bundle)
        if bundle_samples:
            type_schema = infer_type_schema(label, bundle_samples)
        else:
            type_schema = guess_type_schema(bundle, label)
        if type_schema is None:
            logger.info("Skipping node--%s: none of its content has a formatted text field", bundle)
        else:
            schema[bundle] = type_schema
    return schema


def fallback_schema(content_types: Iterable[str]) -> Schema:
    """The pre-discovery mapping, for when the site can't be read."""
    return {t: FALLBACK_TYPES.get(t) or guess_type_schema(t, humanize(t)) for t in content_types}


def select_types(schema: Schema, enabled: Iterable[str]) -> Schema:
    """The installer's enabled subset of *schema*, in schema order.

    Empty *enabled* means every type. So does a selection naming only types
    the site no longer has: a stale setting mustn't leave Dreachy knowing
    nothing. (Backend.schema warns about that, once.)
    """
    wanted = set(enabled)
    selected = {t: s for t, s in schema.items() if t in wanted}
    return selected or dict(schema)
