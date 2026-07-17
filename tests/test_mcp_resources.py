from fairentry.mcp import stdio_server


def test_widget_resource_can_be_listed_and_read():
    listed = stdio_server.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
    uri = listed["result"]["resources"][0]["uri"]

    read = stdio_server.handle({
        "jsonrpc": "2.0",
        "id": 2,
        "method": "resources/read",
        "params": {"uri": uri},
    })

    assert "FairEntry" in read["result"]["contents"][0]["text"]
    assert read["result"]["contents"][0]["mimeType"] == "text/html"
