"""Fixture plugins for the plugin host integration suite.

Each subdirectory is a fully-formed plugin package -- ``manifest.yml`` +
``__init__.py`` exporting ``Plugin`` -- so the host's worker can import
and run it through the production code path. The fixtures intentionally
exercise every failure surface listed in Backend Spec §6.3.4 (timeout,
:class:`PluginError`, :class:`PluginInputRequired`, policy violation,
crash containment) plus the canonical :class:`hello_plugin` from §6.5.

Plugins under :mod:`tests.fixtures.plugins._failures` are not safe to run
through ``import``-time discovery against a real registry -- they are
designed to crash, hang, or violate the §6.1.5 forbidden-write set when
their ``transform`` runs.
"""
