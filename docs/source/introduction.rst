Introduction
============

.. contents::
   :local:
   :depth: 1

==================
Overview
==================

The goal of this format is to facilitate interchange of annotations and notes between different review systems or provide a very simple offline review format for a simple review system.

TODO.


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

