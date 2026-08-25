from hylogger.selections import add_holes, create_selection, load_selection, remove_holes


def test_selection_is_normalised_and_sorted(app_config) -> None:
    create_selection(app_config, "pilot", [" b02 ", "A01", "a01"])
    add_holes(app_config, "pilot", ["C03"])
    remove_holes(app_config, "pilot", ["b02"])

    assert load_selection(app_config, "pilot")["holes"] == ["A01", "C03"]
