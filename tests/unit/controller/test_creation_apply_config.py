"""Unit tests for ``CreationController.apply_config`` (live config reload).

The controller reads its config at create-time, so a live settings Save
only needs to swap ``self._config`` (and re-inject the plugin host when
the coordinator rebuilt it) for the change to take effect without a tray
relaunch.
"""

from __future__ import annotations

from types import SimpleNamespace

from exlab_wizard.controller.creation import CreationController


def _controller(config: object) -> CreationController:
    # The constructor only stores its collaborators, so lightweight
    # stand-ins are enough to exercise apply_config.
    return CreationController(
        config=config,
        validator=SimpleNamespace(),
        template_engine=SimpleNamespace(),
        plugin_host=SimpleNamespace(name="original"),
        cache_creation=SimpleNamespace(),
        cache_equipment=SimpleNamespace(),
    )


def test_apply_config_swaps_config() -> None:
    old = SimpleNamespace(tag="old")
    controller = _controller(old)
    assert controller._config is old

    new = SimpleNamespace(tag="new")
    controller.apply_config(new)
    assert controller._config is new


def test_apply_config_reinjects_plugin_host_only_when_given() -> None:
    controller = _controller(SimpleNamespace())
    original_host = controller._plugin_host

    # Unprovided -> keep the existing host.
    controller.apply_config(SimpleNamespace())
    assert controller._plugin_host is original_host

    # Provided -> swap it in.
    new_host = SimpleNamespace(name="rebuilt")
    controller.apply_config(SimpleNamespace(), plugin_host=new_host)
    assert controller._plugin_host is new_host
