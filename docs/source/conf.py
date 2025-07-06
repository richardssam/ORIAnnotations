import os
import sys

project_root = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "..")

manifest_path = os.environ.get("OTIO_PLUGIN_MANIFEST_PATH", "")
if manifest_path:
    manifest_path += os.pathsep
os.environ["OTIO_PLUGIN_MANIFEST_PATH"] = manifest_path + os.path.join(
    project_root, "otio_event_plugin", "plugin_manifest.json"
)

path = os.path.abspath('../../python')
sys.path.insert(0, path)  # Adjust if layout changes
path = os.path.abspath('../../otio_event_plugin')
sys.path.insert(0, path)  # Adjust if layout changes


# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = 'ORIAnnotations'
copyright = '2025, Sam Richards'
author = 'Sam Richards'
release = '0.1.0'

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.napoleon',
    'sphinx.ext.viewcode',
    'sphinxmermaid',
]

autodoc_mock_imports = ["PySide2", "scipy"]

templates_path = ['_templates']

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = 'alabaster'
html_static_path = ['_static']
