"""OTIO media_linker plugin: fall back to an HTTP-served copy of a clip's
media when the local copy is unavailable.

Activated via OTIO's standard ``MediaLinkingPolicy.ForceDefaultLinker``
(the default already used by every ``read_from_string``/``read_from_file``
call in the sync pipeline) by setting ``OTIO_DEFAULT_MEDIA_LINKER=http_fallback``.
Fully inert unless both :data:`ORI_MEDIA_LOCAL_ROOT` and
:data:`ORI_MEDIA_HTTP_BASE_URL` are also set, so registering this plugin's
manifest entry has no effect on any process that doesn't opt in.
"""

import os

import opentimelineio as otio

#: Env var naming the local directory clip basenames are checked against.
ORI_MEDIA_LOCAL_ROOT = "ORI_MEDIA_LOCAL_ROOT"

#: Env var naming the HTTP base URL clip basenames are served from on fallback.
ORI_MEDIA_HTTP_BASE_URL = "ORI_MEDIA_HTTP_BASE_URL"


def link_media_reference(in_clip, media_linker_argument_map):
    """Return a media reference for *in_clip*, substituting an HTTP URL if
    the clip's local media is unavailable.

    The local/remote check is based on the media's basename under
    :data:`ORI_MEDIA_LOCAL_ROOT`, not on the scheme of the clip's current
    ``target_url`` — this makes the check equally valid whether the clip
    currently points at a local path or at a previously-substituted HTTP
    URL, so availability is re-evaluated fresh on every call rather than
    cached from an earlier decision.

    :param in_clip: The clip OTIO is resolving media for.
    :type in_clip: :class:`~opentimelineio.schema.Clip`
    :param media_linker_argument_map: Linker arguments from the adapter
        call site; unused by this linker (configuration comes from the
        environment instead, since call sites across the sync pipeline
        don't pass a custom argument map).
    :returns: A replacement media reference, or the clip's existing
        reference if this linker is unconfigured or doesn't apply.
    :rtype: :class:`~opentimelineio.core.MediaReference`
    """
    local_root = os.environ.get(ORI_MEDIA_LOCAL_ROOT)
    http_base_url = os.environ.get(ORI_MEDIA_HTTP_BASE_URL)
    ref = in_clip.media_reference

    if not local_root or not http_base_url:
        return ref
    if not isinstance(ref, otio.schema.ExternalReference) or not ref.target_url:
        return ref

    basename = os.path.basename(ref.target_url)
    local_path = os.path.join(local_root, basename)

    if os.path.exists(local_path):
        target_url = local_path
    else:
        target_url = http_base_url.rstrip("/") + "/" + basename

    if target_url == ref.target_url:
        return ref

    return otio.schema.ExternalReference(
        target_url=target_url,
        available_range=ref.available_range,
    )
