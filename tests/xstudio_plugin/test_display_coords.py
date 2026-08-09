import sys
import os
import unittest
import json
from unittest.mock import MagicMock

# Setup paths
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.append(os.path.join(repo_root, 'python'))
sys.path.append(os.path.join(repo_root, 'xstudio_plugin'))

from ori_sync.display_sync import DisplaySyncController

class TestDisplayCoords(unittest.TestCase):
    def test_read_xs_display_state_free(self):
        plugin = MagicMock()
        controller = DisplaySyncController(plugin)
        controller._xs_base_scale = 5.0

        vp = MagicMock()
        controller.get_viewport = MagicMock(return_value=vp)

        cp = MagicMock()
        cp.exposure.value.return_value = 0.0
        cp.channel.value.return_value = "RGBA"
        vp.colour_pipeline = cp

        serialise_msg = MagicMock()
        serialise_msg.dump.return_value = '{"base": {"scale": 10.0, "translate": ["vec3", 1, 0.2, -0.1, 0.0], "size": ["vec2", 1, 1920, 1080]}}'
        plugin.connection.request_receive_timeout.return_value = [serialise_msg]

        fit_attr = MagicMock()
        fit_attr.value.return_value = "Off"
        vp.get_attribute.return_value = fit_attr

        state = controller.read_xs_display_state()
        self.assertAlmostEqual(state["zoom"], 2.0)
        # xs raw translate is 2 units per protocol unit on both axes (no
        # aspect term — see _XS_PAN_UNITS_PER_PROTOCOL_UNIT), x negated to
        # match RV's opposite pan convention.
        self.assertAlmostEqual(state["pan"][0], -0.1)
        self.assertAlmostEqual(state["pan"][1], -0.05)

    def test_read_xs_display_state_fit(self):
        plugin = MagicMock()
        controller = DisplaySyncController(plugin)
        controller._xs_base_scale = 5.0

        vp = MagicMock()
        controller.get_viewport = MagicMock(return_value=vp)

        cp = MagicMock()
        cp.exposure.value.return_value = 0.0
        cp.channel.value.return_value = "RGBA"
        vp.colour_pipeline = cp

        serialise_msg = MagicMock()
        serialise_msg.dump.return_value = '{"base": {"scale": 6.0, "translate": ["vec3", 1, 0.0, 0.0, 0.0], "size": ["vec2", 1, 1920, 1080]}}'
        plugin.connection.request_receive_timeout.return_value = [serialise_msg]

        fit_attr = MagicMock()
        fit_attr.value.return_value = "Best"
        vp.get_attribute.return_value = fit_attr

        state = controller.read_xs_display_state()
        self.assertAlmostEqual(state["zoom"], 1.0)
        self.assertEqual(state["pan"], [0.0, 0.0])
        self.assertEqual(controller._xs_base_scale, 6.0)  # Dynamically updated!

    def test_pan_read_write_are_inverses(self):
        """apply_display_state's write conversion must invert read_xs_display_state's."""
        plugin = MagicMock()
        controller = DisplaySyncController(plugin)
        controller._xs_base_scale = 5.0

        vp = MagicMock()
        controller.get_viewport = MagicMock(return_value=vp)
        vp.colour_pipeline.exposure.value.return_value = 0.0
        vp.colour_pipeline.channel.value.return_value = "RGBA"
        fit_attr = MagicMock()
        fit_attr.value.return_value = "Off"
        vp.get_attribute.return_value = fit_attr

        translate_x, translate_y = 0.2, -0.1
        serialise_msg = MagicMock()
        serialise_msg.dump.return_value = (
            '{"base": {"scale": 10.0, "translate": '
            f'["vec3", 1, {translate_x}, {translate_y}, 0.0], '
            '"size": ["vec2", 1, 1920, 1080]}}'
        )
        plugin.connection.request_receive_timeout.return_value = [serialise_msg]

        protocol_pan = controller.read_xs_display_state()["pan"]

        controller.apply_display_state({"pan": protocol_pan, "zoom": 2.0})
        written_x, written_y = vp.pan
        self.assertAlmostEqual(written_x, translate_x, places=5)
        self.assertAlmostEqual(written_y, translate_y, places=5)

if __name__ == "__main__":
    unittest.main()
