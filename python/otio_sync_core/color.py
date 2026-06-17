"""Color pipeline metadata schema, vocabulary parsing, and resolution.

This module bridges the OTIO *Color Pipeline Model* RFC through OTIO ``metadata``
until the native fields land in OTIO core.  The metadata keys mirror the RFC
field names **verbatim** so a future migration to native ``Timeline.color`` /
``Composable.color_space`` fields is a key move, not a reshape:

* ``Timeline.metadata["color"]`` — a config group with ``config``,
  ``working_space`` and ``output_space`` string entries.
* ``Composable.metadata["color_space"]`` — a clip's input colorspace as a
  single vocabulary-prefixed string (e.g. ``"ocio:ACEScg"``).

The module carries **no rendering logic** and resolves nothing to a transform —
it only parses names, reads the metadata defensively, and applies the
hierarchical resolution rule.  Host adapters (OpenRV, xStudio) turn the resolved
name into an actual color transform against their own OCIO config.

It deliberately does **not** import ``opentimelineio`` so it stays importable in
isolation; it only touches the ``.metadata`` mapping of the objects passed in.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Metadata keys — mirror the RFC field names verbatim.
# ---------------------------------------------------------------------------

#: Key of the timeline-level color config group: ``timeline.metadata[COLOR_GROUP]``.
COLOR_GROUP = "color"
#: Sub-keys of the timeline color group.
CONFIG = "config"
WORKING_SPACE = "working_space"
OUTPUT_SPACE = "output_space"

#: Key of a composable's input colorspace: ``clip.metadata[COLOR_SPACE]``.
COLOR_SPACE = "color_space"

# ---------------------------------------------------------------------------
# Vocabulary convention.
# ---------------------------------------------------------------------------

#: Vocabulary assumed for a bare (unprefixed) name.
DEFAULT_VOCABULARY = "ocio"
#: Vocabularies actively resolved by host adapters in v1.  Other tags
#: (``cicp``, ``resolve``, ``aces``, ``custom``, …) are preserved verbatim.
RESOLVED_VOCABULARIES = ("ocio", "interop")

#: A valid vocabulary tag is lowercase ASCII letters, digits and underscores.
_TAG_RE = re.compile(r"^[a-z0-9_]+$")


def parse_colorspace(value: str) -> "tuple[str, str]":
    """Split a colorspace string into ``(vocabulary, name)``.

    The text before the first ``:`` is the vocabulary tag **iff** it is a valid
    tag (ASCII ``[a-z0-9_]``); the remainder is the name.  A string with no
    colon — or whose leading segment is not a valid tag — is treated as a bare
    name in :data:`DEFAULT_VOCABULARY`.  This matches the RFC rule that names
    legitimately containing a colon (e.g. ``"ocio:Utility - Curve - sRGB"``)
    must be prefixed, while unprefixed names never are.

    :param value: The colorspace string (e.g. ``"ocio:ACEScg"``, ``"ACEScg"``).
    :returns: ``(vocabulary, name)``.  ``vocabulary`` is the lowercased tag, or
        :data:`DEFAULT_VOCABULARY` for a bare name.
    """
    head, sep, tail = value.partition(":")
    if sep and _TAG_RE.match(head):
        return head, tail
    return DEFAULT_VOCABULARY, value


def is_resolved_vocabulary(value: str) -> bool:
    """Return whether *value*'s vocabulary is one a host adapter resolves in v1.

    Unknown vocabularies are valid and MUST be preserved verbatim, but host
    adapters are only expected to resolve :data:`RESOLVED_VOCABULARIES`.

    :param value: A colorspace string.
    :returns: ``True`` if the vocabulary is in :data:`RESOLVED_VOCABULARIES`.
    """
    vocab, _ = parse_colorspace(value)
    return vocab in RESOLVED_VOCABULARIES


# ---------------------------------------------------------------------------
# Defensive metadata reads.
#
# One OTIO build throws on nested metadata reads; the color group is a shallow
# dict of strings, but reads are still wrapped so a malformed/absent group can
# never abort a load or a sync apply.
# ---------------------------------------------------------------------------


def _metadata(obj: Any) -> "dict | None":
    """Return *obj*'s metadata as a plain mapping, or ``None`` if unavailable."""
    try:
        md = obj.metadata
    except Exception:
        return None
    return md if hasattr(md, "get") else None


def read_timeline_color(timeline: Any) -> dict:
    """Return the timeline color group as a plain ``dict`` (empty if unset).

    :param timeline: An OTIO ``Timeline`` (or any object with ``.metadata``).
    :returns: A dict possibly containing :data:`CONFIG`, :data:`WORKING_SPACE`
        and :data:`OUTPUT_SPACE`.  Empty when the timeline is unmanaged.
    """
    md = _metadata(timeline)
    if md is None:
        return {}
    try:
        group = md.get(COLOR_GROUP)
    except Exception:
        return {}
    if not hasattr(group, "get"):
        return {}
    return {k: group.get(k) for k in (CONFIG, WORKING_SPACE, OUTPUT_SPACE) if group.get(k)}


def read_color_space(obj: Any) -> "str | None":
    """Return a composable's ``color_space`` string, or ``None`` if unset.

    :param obj: An OTIO ``Composable`` (or any object with ``.metadata``).
    :returns: The non-empty colorspace string, or ``None``.
    """
    md = _metadata(obj)
    if md is None:
        return None
    try:
        value = md.get(COLOR_SPACE)
    except Exception:
        return None
    return value or None


# ---------------------------------------------------------------------------
# Hierarchical resolution.
# ---------------------------------------------------------------------------


def resolve_input_colorspace(
    clip: Any,
    timeline: Any = None,
    host_default: "str | None" = None,
) -> "str | None":
    """Resolve a clip's effective input colorspace.

    Resolution order, per the color-pipeline-sync capability:

    1. the clip's own ``color_space`` if set;
    2. otherwise the timeline's ``working_space``;
    3. otherwise *host_default*.

    No media-reference field or provenance data is consulted.

    :param clip: The OTIO ``Clip`` whose input space is being resolved.
    :param timeline: The owning ``Timeline``, consulted for ``working_space``.
        Optional; pass ``None`` to skip straight to *host_default*.
    :param host_default: The host's fallback colorspace name.
    :returns: The effective colorspace string, or ``None`` when nothing is set.
    """
    own = read_color_space(clip)
    if own:
        return own
    if timeline is not None:
        working = read_timeline_color(timeline).get(WORKING_SPACE)
        if working:
            return working
    return host_default
