import json
import sys
import os
import glob

def map_command_to_schema(command: str) -> str:
    mapping = {
        "SESSION": "LiveSession.1",
        "PLAYBACK_SETTINGS": "PLAYBACK_SETTINGS_1.0",
        "DISPLAY_SETTINGS": "DISPLAY_SETTINGS_1.0",
        "SELECTION": "SELECTION_1.0",
        "ADD_TIMELINE": "TIMELINE_1.0",
        "RENAME_TIMELINE": "TIMELINE_1.0",
        "PARTIAL_ANNOTATION": "Annotation.1",
        "OTIO_SESSION": "OTIO_SESSION_1.0"
    }
    return mapping.get(command, command)

def convert_line(line: str) -> str:
    try:
        data = json.loads(line.strip())
    except json.JSONDecodeError:
        return line # If it's empty or invalid, return as-is
    
    if "payload" not in data or "command" not in data["payload"]:
        return line # Already converted or malformed
    
    old_msg = data["payload"]
    command = old_msg.get("command")
    event = old_msg.get("event")
    
    # Handling PARTIAL_ANNOTATION which had no event in old flat format
    if command == "PARTIAL_ANNOTATION" and not event:
        event = "PARTIAL"
        
    # Some older commands might not have event
    if not event and command == "ADD_TIMELINE":
        event = "ADD_TIMELINE"
    if not event and command == "RENAME_TIMELINE":
        event = "RENAME_TIMELINE"
        
    new_msg = {
        "session": old_msg.get("session_id", ""),
        "source_guid": old_msg.get("source_guid", ""),
        "payload": {
            "command_schema": map_command_to_schema(command),
            "command": {
                "event": event,
                "payload": old_msg.get("payload", {})
            }
        }
    }
    
    if event == "I_AM_MASTER":
        new_msg["schema"] = "SYNC_REVIEW_1.0"
        
    # Replace old payload with new payload
    data["payload"] = new_msg
    return json.dumps(data)

def process_file(filepath: str):
    print(f"Converting {filepath}...")
    with open(filepath, "r") as f:
        lines = f.readlines()
        
    converted_lines = [convert_line(l) for l in lines]
    
    with open(filepath, "w") as f:
        for l in converted_lines:
            f.write(l + "\n")

if __name__ == "__main__":
    files = sys.argv[1:]
    for f in files:
        if os.path.isfile(f):
            process_file(f)
        elif os.path.isdir(f):
            for path in glob.glob(os.path.join(f, "*.jsonl")):
                process_file(path)
    print("Done converting.")
