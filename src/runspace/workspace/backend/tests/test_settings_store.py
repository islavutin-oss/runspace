"""Values behind the settings screen.

The screen has always fetched `/api/workspace/settings`; nothing served it, so
every workspace showed empty text boxes, zeroed numbers and toggles that reset
on reload. The sections were declared and the values had nowhere to live.
"""

from __future__ import annotations

import json

import pytest

from runspace.workspace.backend.settings_store import SettingsStore, defaults_from_schema

SCHEMA = [
    {"id": "note", "type": "text", "field": "editorial_note", "default": "House style"},
    {"id": "cron", "type": "schedule", "field": "digest_cron", "default": "0 7 * * *"},
    {
        "id": "bar",
        "type": "number_pair",
        "fields": [
            {"key": "window_days", "default": 7},
            {"key": "price_ceiling", "default": 20},
        ],
    },
    {
        "id": "approvals",
        "type": "toggle_list",
        "items": [{"key": "publish", "value": True}, {"key": "read", "value": False}],
    },
    {"id": "src", "type": "key_value", "fields": [{"key": "Models", "value": "openrouter"}]},
    {"id": "gw", "type": "gateway_status", "gateways": [{"id": "web", "status": "connected"}]},
]


@pytest.fixture
def store(tmp_path):
    return SettingsStore("acme", root=tmp_path)


class TestDefaults:
    def test_every_value_shape_is_read_from_its_own_place(self):
        d = defaults_from_schema(SCHEMA)
        assert d["editorial_note"] == "House style"  # text: field
        assert d["digest_cron"] == "0 7 * * *"  # schedule: field
        assert d["window_days"] == 7  # number_pair: fields[]
        assert d["publish"] is True  # toggle_list: items[]
        assert d["Models"] == "openrouter"  # key_value: fields[]

    def test_a_display_only_section_contributes_no_value(self):
        """gateway_status shows state; it is not an editable field, and
        inventing a key for it would put a phantom in every saved file."""
        assert "web" not in defaults_from_schema(SCHEMA)

    def test_an_empty_schema_is_not_an_error(self):
        assert defaults_from_schema([]) == {}
        assert defaults_from_schema(None) == {}


class TestPersistence:
    def test_a_fresh_workspace_opens_on_its_declared_defaults(self, store):
        """This is the bug: with no endpoint the screen showed 0 and blank
        where the author had specified 7 and a cron line."""
        v = store.load(SCHEMA)
        assert v["window_days"] == 7 and v["digest_cron"] == "0 7 * * *"

    def test_saved_values_survive_a_reload(self, tmp_path):
        SettingsStore("acme", root=tmp_path).save({"window_days": 14}, SCHEMA)
        assert SettingsStore("acme", root=tmp_path).load(SCHEMA)["window_days"] == 14

    def test_saving_one_section_does_not_wipe_another(self, store):
        store.save({"window_days": 14}, SCHEMA)
        store.save({"editorial_note": "Terse"}, SCHEMA)
        v = store.load(SCHEMA)
        assert v["window_days"] == 14 and v["editorial_note"] == "Terse"

    def test_a_saved_value_overrides_its_default(self, store):
        store.save({"digest_cron": "0 9 * * 1"}, SCHEMA)
        assert store.load(SCHEMA)["digest_cron"] == "0 9 * * 1"

    def test_a_section_added_later_still_shows_its_default(self, store):
        """Someone saved before the section existed. Layering defaults under
        saved values is what stops the new field rendering blank."""
        store.save({"window_days": 14}, [SCHEMA[2]])
        assert store.load(SCHEMA)["editorial_note"] == "House style"

    def test_one_tenant_cannot_read_another(self, tmp_path):
        SettingsStore("acme", root=tmp_path).save({"window_days": 99}, SCHEMA)
        assert SettingsStore("globex", root=tmp_path).load(SCHEMA)["window_days"] == 7

    def test_a_corrupt_file_falls_back_to_defaults(self, store):
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.path.write_text("{not json", encoding="utf-8")
        assert store.load(SCHEMA)["window_days"] == 7

    def test_a_non_object_body_is_refused(self, store):
        with pytest.raises(ValueError):
            store.save(["not", "an", "object"], SCHEMA)

    def test_the_write_is_atomic(self, store):
        """A half-written file is what a crash mid-save would otherwise
        leave, and the next load would have to recover from it."""
        store.save({"window_days": 5}, SCHEMA)
        assert json.loads(store.path.read_text())["window_days"] == 5
        assert not store.path.with_suffix(".tmp").exists()
