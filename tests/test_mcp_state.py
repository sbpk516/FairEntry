from fairentry.mcp import state


def test_add_list_close_position(tmp_path):
    path = tmp_path / "state.json"

    pos = state.add_position("atat", shares=12, entry_price=30.5, path=path)
    listed = state.list_portfolio(path)

    assert pos["ticker"] == "ATAT"
    assert listed["count"] == 1
    assert listed["positions"][0]["shares"] == 12

    closed = state.close_position(pos["id"], "test close", path)

    assert closed["status"] == "closed"
    assert state.list_portfolio(path)["count"] == 0


def test_save_and_filter_notes(tmp_path):
    path = tmp_path / "state.json"

    state.save_note("rely", "Watch remittance volume.", tag="thesis", path=path)
    state.save_note("atat", "Check occupancy.", tag="risk", path=path)

    notes = state.list_notes("RELY", path)

    assert notes["count"] == 1
    assert notes["notes"][0]["tag"] == "thesis"
