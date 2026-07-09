import rv.qtutils
import rv.commands
import rv.rvtypes
import os
import sys

# Global flag to prevent double execution if multiple render events fire
grabbed = False
mode_instance = None

def log(msg):
    try:
        sys.__stdout__.write(msg + '\n')
        sys.__stdout__.flush()
    except Exception:
        pass

class GrabberMode(rv.rvtypes.MinorMode):
    def __init__(self):
        rv.rvtypes.MinorMode.__init__(self)
        self.init("grabber_mode", [
            ("after-render", self.on_render_event, "Handle after-render event")
        ], None, None)

    def on_render_event(self, event):
        # We are called in the rendering sequence. Defer the actual grab to the next
        # event loop tick, releasing the render thread context and GIL first.
        try:
            from PySide6 import QtCore
        except ImportError:
            from PySide2 import QtCore
        
        log("after-render event callback triggered, scheduling deferred grab...")
        QtCore.QTimer.singleShot(0, self.do_actual_grab)

    def do_actual_grab(self):
        global grabbed
        if grabbed:
            return
        grabbed = True
        
        try:
            log("Deferred grab execution started...")
            try:
                from PySide6 import QtWidgets
                import shiboken6
            except ImportError:
                from PySide2 import QtWidgets
                import shiboken2 as shiboken6

            output_path = os.environ.get("GRAB_OUTPUT_PATH", "grab.png")
            ptr = rv.commands.sessionGLView()
            log(f"GL view pointer: {ptr}")
            if ptr:
                view = shiboken6.wrapInstance(ptr, QtWidgets.QWidget)
                log(f"Grabbing viewport to {output_path}...")
                view.grab().save(output_path)
                log("Successfully saved!")
            else:
                log("Error: GL view pointer is 0!")
        except Exception as e:
            import traceback
            log(f"Exception during grab:\n{traceback.format_exc()}")
        finally:
            log("do_actual_grab finished, exiting")
            os._exit(0)

def do_grab():
    global mode_instance
    try:
        log("do_grab started")
        
        frame_str = os.environ.get("GRAB_FRAME", "5")
        frame = int(frame_str)
        
        log("Instantiating GrabberMode...")
        mode_instance = GrabberMode()
        log("Activating GrabberMode...")
        mode_instance.toggle()
        
        log(f"Setting frame to {frame} and redrawing...")
        rv.commands.setFrame(frame)
        rv.commands.redraw()
        
    except Exception as e:
        log(f"Exception in do_grab: {e}")
        os._exit(1)

log("grab_frame.py script loaded")
# Wait 2 seconds to make sure window is constructed and active
try:
    from PySide6 import QtCore
except ImportError:
    from PySide2 import QtCore
QtCore.QTimer.singleShot(2000, do_grab)
log("QTimer scheduled")
