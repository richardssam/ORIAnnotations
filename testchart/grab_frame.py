import rv.qtutils
import rv.commands
import os
import sys
from PySide6 import QtCore

def log(msg):
    try:
        open('/Users/sam/.gemini/antigravity-ide/brain/52954da0-fc59-48d0-a273-909269e0c024/scratch/grab_log.txt', 'a').write(msg + '\n')
    except Exception:
        pass

def do_grab():
    try:
        log("do_grab started")
        frame_str = os.environ.get("GRAB_FRAME", "5")
        frame = int(frame_str)
        output_path = os.environ.get("GRAB_OUTPUT_PATH", "grab.png")
        log(f"Setting frame to {frame}...")
        rv.commands.setFrame(frame)
        rv.commands.redraw()
        
        ptr = rv.commands.sessionGLView()
        log(f"GL view pointer: {ptr}")
        if ptr:
            from PySide6.QtWidgets import QWidget
            import shiboken6
            view = shiboken6.wrapInstance(ptr, QWidget)
            log(f"Grabbing GL view to {output_path}...")
            view.grab().save(output_path)
            log("Successfully saved!")
        else:
            log("Error: GL view pointer is 0!")
            # Try to grab the session window
            win = rv.qtutils.sessionWindow()
            if win:
                log("Grabbing session window instead...")
                win.grab().save(output_path)
            else:
                log("Error: sessionWindow is also None!")
    except Exception as e:
        import traceback
        log(f"Exception during grab:\n{traceback.format_exc()}")
    finally:
        log("do_grab finished, exiting")
        os._exit(0)

# Clear log
try:
    if os.path.exists('/Users/sam/.gemini/antigravity-ide/brain/52954da0-fc59-48d0-a273-909269e0c024/scratch/grab_log.txt'):
        os.remove('/Users/sam/.gemini/antigravity-ide/brain/52954da0-fc59-48d0-a273-909269e0c024/scratch/grab_log.txt')
except Exception:
    pass

log("grab_frame.py script loaded")
# Wait 2 seconds to make sure window is constructed and rendered
QtCore.QTimer.singleShot(2000, do_grab)
log("QTimer scheduled")
