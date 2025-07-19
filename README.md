# ORIAnnotations
A toolkit for sharing annotations between review systems.

See: [docs/html/introduction.html](https://richardssam.github.io/ORIAnnotations/docs/html/introduction.html)

## Requirements

Python libraries:

```
pip install opentimelineio
```

For building the docs:
```
pip install -U sphinx sphinx-mermaid
```

Creating the docs:
```
cd docs
make html
```

## Example OTIO files

   * examples/testexport/annotationreview.otio is an example OTIO annotation file.
   * examples/testsession.rv is the rv-session file that was used to generate this file, and the media is in that folder.

## Sample RV plugin exporter/importer

The directory rvplugin contains a plugin for OpenRV and RV, which allows the user to load a number of clips and export them with their annotations as a custom OTIO file. And similarly be able to reload the OTIO file back into OpenRV re-creating the annotations.

The plugin can be loaded using the OpenRV package manager by loading in the oriannotations.zip package.

If you need to rebuild the oriannoations.zip package, the script "makepackage.csh" should re-create the package given the files in the git repo.

