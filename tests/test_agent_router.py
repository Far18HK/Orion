from services.agent_router import PROFILES, filter_specs, route


def _spec(name: str) -> dict:
    return {"type": "function", "function": {"name": name}}


def test_dispatcher_selects_specialists():
    assert route("crea un evento y un anuncio en Discord").name == "discord"
    assert route("lee este zip y crea un archivo json").name == "files"
    assert route("investiga las noticias actuales").name == "research"


def test_profile_filters_tools():
    specs = filter_specs([_spec("read_file"), _spec("write_discord_channel"), _spec("web_search")], PROFILES["files"])
    assert [item["function"]["name"] for item in specs] == ["read_file"]
