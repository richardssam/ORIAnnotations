import sys

with open("runner.py", "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    new_lines.append(line)
    if 'if action == "draw_annotation":' in line:
        pass
    if 'self._wait_for_media(app_ports)' in line:
        pass
    if 'continue' in line and 'if action == "draw_annotation":' in "".join(lines[lines.index(line)-2:lines.index(line)]):
        pass

# Actually let's just find the exact index.
target_idx = -1
for i, line in enumerate(lines):
    if 'if action == "draw_annotation":' in line:
        target_idx = i + 3
        break

if target_idx != -1:
    code = """
                    if action == "expect_broadcast_and_state":
                        target = cmd.get("app")
                        expected_view_mode = cmd.get("view_mode")
                        expected_playback_mode = cmd.get("playback_mode")
                        port = next((p for n, p in app_ports if n == target), None)
                        if port is None:
                            logging.error(f"expect_broadcast_and_state: no app named {target!r}")
                            failed = True
                            break
                        
                        time.sleep(float(cmd.get("settle", 1.0)))
                        inspector_state = self.fetch_state(port) or {}
                        full_state = self.fetch_full_state(port) or {}

                        ground_truth_view_mode = inspector_state.get("view_mode")
                        ground_truth_playback_mode = inspector_state.get("playback_mode")
                        wire_state = full_state.get("playback_state") or {}
                        wire_view_mode = wire_state.get("view_mode")
                        wire_playback_mode = wire_state.get("playback_mode")
                        
                        logging.info(f"  -> {target} ground-truth (fetch_state): view_mode={ground_truth_view_mode!r}, playback_mode={ground_truth_playback_mode!r}")
                        logging.info(f"  -> {target} wire broadcast (full_state): view_mode={wire_view_mode!r}, playback_mode={wire_playback_mode!r}")
                        
                        if ground_truth_view_mode != expected_view_mode or ground_truth_playback_mode != expected_playback_mode:
                            logging.error(f"Ground-truth state mismatch: expected {expected_view_mode!r}/{expected_playback_mode!r}, got {ground_truth_view_mode!r}/{ground_truth_playback_mode!r}")
                            failed = True
                            
                        if wire_view_mode != expected_view_mode or wire_playback_mode != expected_playback_mode:
                            logging.error(f"Wire broadcast mismatch: expected {expected_view_mode!r}/{expected_playback_mode!r}, got {wire_view_mode!r}/{wire_playback_mode!r}")
                            failed = True
                            
                        if failed:
                            break
                        continue
"""
    lines.insert(target_idx, code)
    with open("runner.py", "w") as f:
        f.writelines(lines)
    print("Patched runner.py successfully.")
else:
    print("Could not find insertion point.")
