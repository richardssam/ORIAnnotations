import opentimelineio as otio

class OTIOSyncProxy:
    def __init__(self, obj, manager, parent_path=""):
        object.__setattr__(self, "_obj", obj)
        object.__setattr__(self, "_manager", manager)
        object.__setattr__(self, "_path", parent_path)

    @property
    def __class__(self):
        return self._obj.__class__

    def __getattr__(self, name):
        val = getattr(self._obj, name)
        
        if isinstance(val, otio.core.SerializableObject):
            return OTIOSyncProxy(val, self._manager, "")
            
        return val

    def __setattr__(self, name, value):
        if name in ["_obj", "_manager", "_path"]:
            object.__setattr__(self, name, value)
            return

        setattr(self._obj, name, value)
        
        guid = None
        if isinstance(self._obj, otio.core.SerializableObject):
            if "sync" in self._obj.metadata and "guid" in self._obj.metadata["sync"]:
                guid = self._obj.metadata["sync"]["guid"]
                
        if guid:
            path = name if not self._path else f"{self._path}/{name}"
            self._manager.set_property(guid, path, value)

    def __repr__(self):
        return repr(self._obj)

    def __str__(self):
        return str(self._obj)
