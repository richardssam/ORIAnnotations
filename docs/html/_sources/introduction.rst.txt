Introduction
============

.. contents::
   :local:
   :depth: 1

==================
Overview
==================

The goal of this format is to facilitate interchange of annotations and notes between different review systems or provide a very simple offline review format for a simple review system.

The ORIAnnotations module provides a set of helper classes to manage media and annotations, primarily using OpenTimelineIO (OTIO) as the interchange format. This allows for easy integration with various production tracking systems without requiring specialized tools.


Requirements
------------

The main requirement is on Opentimelineio.

.. code-block:: bash

   pip install opentimelineio


However, to build the documentation, you also need:

.. code-block: bash

   pip install -U sphinx sphinx-mermaid


Example Usage
-------------

.. literalinclude:: ../../tests/testannotations.py
   :language: python
   :linenos:

You can run this from the command line, once the libraries are installed with:

.. code-block:: bash

   python test/testannotations.py

See also:
   * https://lf-aswf.atlassian.net/wiki/spaces/PRWG/pages/11274625/OTIO-Based+Synchronized+Review+Messaging
   * https://docs.google.com/document/d/1dmo5Le5elqNNl9p4GpcRPT76BV7r6KYtrapHITD25uA/edit?tab=t.0#heading=h.3jckyebptegj

