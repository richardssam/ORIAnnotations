# otio-media-http-fallback Specification

## Purpose

Lets a peer whose local copy of a clip's media is unavailable substitute an
equivalent HTTP-served copy, using OTIO's standard `media_linker` extension
point rather than changes to either host plugin.

## Requirements

### Requirement: Linker substitutes HTTP media only when local media is unresolvable
The plugin SHALL implement `link_media_reference(in_clip, media_linker_argument_map)`
per OTIO's `MediaLinker` plugin contract. For a clip whose `media_reference`
is an `ExternalReference`, the plugin SHALL check whether the reference's
local-filesystem candidate path exists. If it does not, the plugin SHALL
return a new `ExternalReference` whose `target_url` points at the same
filename served from a configured HTTP base URL, preserving the clip's
`available_range`. If the local path exists, the plugin SHALL return the
clip's existing media reference unchanged.

#### Scenario: Local media present
- **WHEN** the linker runs on a clip whose `target_url` resolves to a file
  that exists on disk
- **THEN** the returned media reference is unchanged from the input

#### Scenario: Local media missing
- **WHEN** the linker runs on a clip whose `target_url` resolves to a local
  path that does not exist on disk
- **THEN** the returned media reference is an `ExternalReference` whose
  `target_url` is the configured HTTP base URL joined with the original
  file's basename, and whose `available_range` matches the original

### Requirement: Local-availability check is re-evaluated on every invocation
The plugin SHALL determine the local candidate path (and therefore whether
to fall back) independently of what `target_url` currently holds — including
when `target_url` is already an HTTP URL from a prior substitution — so that
each call to the linker reflects current local media availability rather
than a cached decision from an earlier call.

#### Scenario: Media reappears locally between two linker invocations
- **GIVEN** a clip was previously linked to an HTTP `target_url` because its
  local media was unavailable
- **WHEN** the linker runs again after the local file becomes available
- **THEN** the returned media reference points back at the local path, not
  the HTTP URL

### Requirement: Substitution does not alter clip identity metadata
`link_media_reference` SHALL return only a replacement `MediaReference`. It
SHALL NOT mutate `in_clip`'s `name`, `metadata` (including any `sync.guid`),
or `source_range` as a side effect.

#### Scenario: Sync GUID preserved across fallback
- **WHEN** the linker substitutes an HTTP media reference for a clip that
  carries `metadata["sync"]["guid"]`
- **THEN** `in_clip.metadata["sync"]["guid"]` is unchanged after the call,
  and the adapter's substitution of `media_reference` is the only change
  observed on the clip
