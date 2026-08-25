#!/usr/bin/env python
# SPDX-License-Identifier: Apache-2.0
"""
Debug tool to inspect running xStudio host and client sessions.

Displays connected status, active playhead information, loaded playlists,
and tracks/clips hierarchies to verify state and selection synchronization.

Usage:
    python debug/xstudio_inspect.py
"""

import os
import sys

# Ensure the embedded xStudio python framework packages are accessible
xstudio_site_packages = "/Users/sam/git/xstudio/build/xSTUDIO.app/Contents/Frameworks/lib/python3.11/site-packages"
if os.path.exists(xstudio_site_packages) and xstudio_site_packages not in sys.path:
    sys.path.insert(0, xstudio_site_packages)

try:
    from xstudio.connection import Connection
    from xstudio.api.session.playlist.timeline import Timeline
    from xstudio.core import viewport_active_media_container_atom, viewport_playhead_atom
    from xstudio.api.session.container import Container
    from xstudio.api.intrinsic.viewport import Viewport
    from xstudio.api.session.playhead import Playhead
except ImportError:
    print("Error: Could not import xstudio python API. Ensure xStudio build exists.")
    sys.exit(1)

# Default port configuration for host and client xStudio instances
PORTS = {
    "Host (Master)": 14441,
    "Client (Peer)": 14442,
}

def inspect_xstudio_instance(name: str, port: int) -> None:
    """Connect to a running xStudio instance and print its internal hierarchy.

    :param name: Human-readable name of the instance (e.g. 'Host (Master)').
    :type name: str
    :param port: TCP port number the instance's API is listening on.
    :type port: int
    """
    print(f"\n==================== {name} (Port: {port}) ====================")
    try:
        conn = Connection(auto_connect=False)
        conn.connect_remote("127.0.0.1", port)
        print("Status: Connected successfully!")

        session = conn.api.session

        # 1. Viewport / Viewed Container Status
        try:
            vc = session.viewed_container
            print(f"Active Sidebar Viewed Container: '{getattr(vc, 'name', 'None')}' (type: {type(vc).__name__ if vc else 'None'})")
        except Exception as e:
            print(f"Active Sidebar Viewed Container: Error reading ({e})")

        try:
            session_actor = session.remote
            result = conn.request_receive_timeout(
                100, session_actor, viewport_active_media_container_atom()
            )[0]
            c = Container(conn, result.actor)
            print(f"Active Viewport Media Container: UUID={result.uuid} type={c.type}")
        except Exception as e:
            print(f"Active Viewport Media Container: Error reading ({e})")

        # 2. Playhead Status
        try:
            vp = Viewport(conn, active_viewport=True)
            ph_actor = conn.request_receive_timeout(
                100, vp.remote, viewport_playhead_atom()
            )[0]
            ph = Playhead(conn, ph_actor)
            print(f"Active Playhead: position={ph.position} playing={ph.playing} fps={ph.frame_rate.fps():.3f}")
        except Exception as e:
            print(f"Active Playhead: Error reading ({e})")

        # 3. Playlists & Tracks Hierarchy
        playlists = session.playlists
        print(f"Playlists Count: {len(playlists)}")
        for pl in playlists:
            print(f"\n  Playlist: '{pl.name}'")
            print(f"    Media items count: {len(pl.media)}")
            for m in pl.media:
                print(f"      - Media: '{m.name}'")
                try:
                    bms = m.ordered_bookmarks()
                    print(f"        Bookmarks count: {len(bms)}")
                    for bm in bms:
                        from xstudio.core import bookmark_detail_atom
                        detail = conn.request_receive(bm.remote, bookmark_detail_atom())[0]
                        print(f"          - Bookmark: Start={detail.start} Duration={detail.duration} Note='{getattr(detail, 'note', '')}'")
                        try:
                            ann = bm.annotation_data
                            if ann:
                                data = ann.get("Data", {})
                                strokes = data.get("pen_strokes", [])
                                captions = data.get("captions", [])
                                print(f"            Annotation: strokes={len(strokes)} captions={len(captions)}")
                            else:
                                print(f"            Annotation: None")
                        except Exception as ae:
                            print(f"            Annotation error: {ae}")
                except Exception as be:
                    print(f"        Error reading bookmarks: {be}")
            try:
                sel = pl.playhead_selection.selected_sources
                if sel:
                    print(f"    Playlist playhead selection: {[s.name for s in sel]}")
            except Exception:
                pass
            print(f"    Containers count: {len(pl.containers)}")
            for container in pl.containers:
                print(f"      - Container: '{container.name}' (type: {type(container).__name__})")
                if isinstance(container, Timeline):
                    try:
                        selected = container.selection
                        if selected:
                            print(f"      Timeline selection: {[getattr(item, 'name', type(item).__name__) for item in selected]}")
                    except Exception as e:
                        print(f"      Error reading timeline selection: {e}")
                    # Dump timeline track details
                    for track in container.stack.children:
                        print(f"        Track: '{track.name}' (type: {track.item_type})")
                        for item in track.children:
                            print(f"          - Clip/Gap: '{item.name}' (type: {type(item).__name__})")
                            if type(item).__name__ == "Clip" and item.media:
                                print(f"            Associated Media name: '{item.media.name}'")
        conn.disconnect()
    except Exception as e:
        print(f"Status: Failed to connect or inspect ({e})")
        print("        Ensure xStudio is running and listening on this port.")

def main() -> None:
    """Main execution entry point."""
    print("Starting xStudio internal state inspection...")
    for name, port in PORTS.items():
        inspect_xstudio_instance(name, port)
    import os
    os._exit(0)

if __name__ == "__main__":
    main()
