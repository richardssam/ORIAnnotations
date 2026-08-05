## ADDED Requirements

### Requirement: xStudio Bookmark Placement Is Floor-Safe

xStudio derives a bookmark's integer frame from its stored `start` time via `FrameRateDuration::frame(flicks)`, which is `static_cast<int>(std::floor(flicks / rate_.to_flicks()))` — it floors and never rounds. When converting a requested clip-local frame number into `BookmarkDetail.start`, the sync codec SHALL request a time strictly inside the target frame's window (`[frame/fps, (frame+1)/fps)`) with enough margin to absorb `datetime.timedelta`'s truncation to microsecond resolution, rather than the frame's exact leading edge (`frame/fps`), which has no such margin and floors down to `frame - 1` for almost any frame that is not an exact multiple of the fps.

#### Scenario: Placing a bookmark on a non-multiple-of-fps frame

- **WHEN** the sync codec places a bookmark for a received annotation whose clip-local frame is not an exact multiple of the media's fps (e.g. frame 29 or 41 at 24fps)
- **THEN** reading the frame back out of the resulting bookmark (via xStudio's own floor-based frame derivation) SHALL yield exactly the requested frame, not `frame - 1`

#### Scenario: Placing a bookmark on frame 0 or another exact multiple of fps

- **WHEN** the sync codec places a bookmark for a received annotation whose clip-local frame is an exact multiple of the media's fps (e.g. frame 0, 24, or 48 at 24fps)
- **THEN** reading the frame back out of the resulting bookmark SHALL still yield exactly the requested frame
