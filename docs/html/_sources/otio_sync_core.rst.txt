otio_sync_core
==============

.. contents::
   :local:
   :depth: 1

``otio_sync_core`` is the core library for coordinating OTIO timeline
synchronisation across a network session. It provides the :class:`SyncManager`
that drives synchronisation, UDP and RabbitMQ network backends, a transparent
proxy for intercepting attribute writes, typed protocol messages, and the
colour-pipeline and annotation codecs.

.. automodule:: otio_sync_core
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:


manager
-------

The :class:`SyncManager` is the central coordinator: it owns the OTIO timeline,
applies incoming sync events, and emits outgoing ones over the configured
network backend.

.. automodule:: otio_sync_core.manager
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:


protocol_messages
-----------------

Typed dataclasses describing the wire-format messages exchanged between sync
participants.

.. automodule:: otio_sync_core.protocol_messages
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:


network
-------

The UDP network backend and the network-protocol interface implemented by all
backends.

.. automodule:: otio_sync_core.network
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:


rabbitmq_network
----------------

A RabbitMQ-backed network transport, an alternative to the UDP backend.

.. automodule:: otio_sync_core.rabbitmq_network
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:


proxy
-----

A transparent proxy that intercepts attribute writes on wrapped OTIO objects so
that local edits can be turned into outgoing sync events.

.. automodule:: otio_sync_core.proxy
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:


patcher
-------

Helpers for converting between OTIO objects and plain dictionaries and for
applying patches to an OTIO timeline.

.. automodule:: otio_sync_core.patcher
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:


color
-----

Colour-pipeline metadata helpers, including recognition of the special OCIO
colorspace.

.. automodule:: otio_sync_core.color
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:


xs_annotation_codec
-------------------

Encoding and decoding of xStudio annotations to and from the OTIO sync format.

.. automodule:: otio_sync_core.xs_annotation_codec
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:
