
ORIAnnotations
==============


This module is a set of helper classes to manage the media, and annotations.

The goal is to use OTIO as the interchange format for annotations, so that it could be used without needing any special tools.
However, its not particularly helpful to directly read the OTIO file if you are trying to import the annotations into a production tracking system, and similarly to correctly create the OTIO file.
So the classes below are used to help create and interpret the OTIO file.

.. mermaid::

    graph TD
        ReviewGroup -- contains --> Media
        ReviewGroup -- contains --> Review
        Review -- contains --> ReviewItem
        ReviewItem -- contains --> Media
        ReviewItem -- contains --> ReviewItemFrame
        ReviewItemFrame -- uses --> SyncEvent
        Media -- creates --> OTIOClip
        ReviewItemFrame -- creates --> OTIOClip
        Review -- creates --> OTIOTrack
        ReviewGroup -- creates --> OTIOTimeline

        subgraph OpenTimelineIO
            OTIOClip
            OTIOTrack
            OTIOTimeline
            SyncEvent
        end

At a high-level:
   * ReviewGroup has a single set of Media which is being reviewed, and one or more Reviews (although typically 1).
   * Each Review has a single ReviewItem with a 1-1 relationship to the media.
   * Each ReviewItem can have either an overall note, or annotations or notes per frame, defined by a ReviewItemFrame
   * Each ReviewItemFrame could be a single PNG file of the annotation, or it could be the instructions to create those annotations using the ORI SyncEvent format.


TODO:
   * The biggest missing part is that we currently assume all media elements are movies, we need to be able to support frame-sequences too.
   * We also need to be given a OTIO Track, as a starting point, to seed a ReviewGroup.

.. automodule:: ORIAnnotations
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:
