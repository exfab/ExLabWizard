"""Declarative sample-data fixtures for the ``-test`` sandbox.

This package owns the single place a developer edits to change the seeded demo
dataset (:mod:`exlab_wizard.sample_data.spec`) and the ``generate_samples``
facade (:mod:`exlab_wizard.sample_data.generator`) that expands it into a real
on-disk tree under the ``-test`` sandbox, writing each folder's metadata
through the production producers so the data cannot drift from the real format.

The facade is re-exported here for callers (the tray flag, the dev command).
``spec`` stays import-safe on its own; importing the facade pulls the generator
(and the producers it wires) only when the symbol is actually used.
"""

from exlab_wizard.sample_data.generator import SampleDataGenerator, generate_samples

__all__ = ["SampleDataGenerator", "generate_samples"]
