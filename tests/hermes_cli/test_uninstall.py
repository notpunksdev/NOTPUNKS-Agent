from hermes_cli.uninstall import remove_wrapper_script


def test_remove_wrapper_script_removes_notpunks_and_legacy_hermes(tmp_path):
    notpunks = tmp_path / "notpunks"
    hermes = tmp_path / "hermes"
    unrelated = tmp_path / "other"
    notpunks.write_text("#!/bin/sh\nexec python -m hermes_cli.main \"$@\"\n")
    hermes.write_text("#!/bin/sh\nexec /opt/hermes-agent/venv/bin/notpunks \"$@\"\n")
    unrelated.write_text("#!/bin/sh\necho untouched\n")

    removed = remove_wrapper_script([notpunks, hermes, unrelated])

    assert removed == [notpunks, hermes]
    assert not notpunks.exists()
    assert not hermes.exists()
    assert unrelated.exists()
