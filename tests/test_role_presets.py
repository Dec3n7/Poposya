"""Пресеты ролей с правами (политика). Настоящая граница — в боте
(_role_preset клампит под права бота, см. test_command_executor). Здесь
проверяем сам курируемый набор: Administrator не раздаём, мод-лестница
кумулятивна, имена прав известны каталогу."""

from src.api import role_presets
from src.api.permissions_catalog import ADMINISTRATOR_BIT, PERM_BITS


def test_presets_have_unique_keys():
    keys = [p["key"] for p in role_presets.PRESETS]
    assert len(keys) == len(set(keys))
    assert "mod_ladder" in keys and "content_hosts" in keys


def test_presets_json_shape():
    data = role_presets.presets_json()
    assert data
    for preset in data:
        assert preset["key"] and preset["name"] and preset["description"]
        assert preset["roles"]
        for role in preset["roles"]:
            assert isinstance(role["permissions"], str)
            int(role["permissions"])  # маска парсится обратно в число
            assert isinstance(role["perm_labels"], list)


def test_no_preset_grants_administrator():
    for preset in role_presets.PRESETS:
        for role in preset["roles"]:
            mask = role_presets._mask(role["perms"])
            assert not (mask & ADMINISTRATOR_BIT)
            assert "administrator" not in role["perms"]


def test_all_perm_names_known_to_catalog():
    for preset in role_presets.PRESETS:
        for role in preset["roles"]:
            for name in role["perms"]:
                assert name in PERM_BITS, name


def test_mod_ladder_is_cumulative():
    ladder = role_presets.get_preset("mod_ladder")
    masks = [role_presets._mask(r["perms"]) for r in ladder["roles"]]
    # каждая следующая ступень включает все права предыдущей и добавляет новые
    for lower, higher in zip(masks, masks[1:], strict=False):  # соседние ступени, offset на 1
        assert higher & lower == lower
        assert higher != lower


def test_trial_step_has_no_kick_or_ban():
    # стажёр — только «остудить и почистить», ничего необратимого
    trial = role_presets.get_preset("mod_ladder")["roles"][0]
    assert "moderate_members" in trial["perms"]
    assert "kick_members" not in trial["perms"]
    assert "ban_members" not in trial["perms"]


def test_content_hosts_stay_lightweight():
    # у контент-ролей нет мод-власти (kick/ban/роли) — их сила на уровне канала
    heavy = {"kick_members", "ban_members", "manage_roles", "manage_guild"}
    for role in role_presets.get_preset("content_hosts")["roles"]:
        assert not (set(role["perms"]) & heavy)


def test_preset_payload_shape():
    preset = role_presets.get_preset("mod_ladder")
    payload = role_presets.preset_payload(preset)
    assert len(payload["roles"]) == len(preset["roles"])
    first = payload["roles"][0]
    assert set(first) == {"name", "color", "hoist", "mentionable", "permissions"}
    assert isinstance(first["permissions"], str)  # строкой, как везде для битовых полей


def test_audience_roles_are_mentionable_and_permissionless():
    # роли-аудитории: пинг без «Пинговать @everyone» => упоминаемые и без прав
    audience = role_presets.get_preset("audience")
    assert audience is not None
    for role in audience["roles"]:
        assert role["mentionable"] is True
        assert role["perms"] == ()
        assert role_presets._mask(role["perms"]) == 0


def test_all_expected_presets_present():
    keys = {p["key"] for p in role_presets.PRESETS}
    assert {
        "mod_ladder",
        "content_hosts",
        "audience",
        "pronouns",
        "notifications",
        "platforms",
        "media",
    } <= keys


def test_self_serve_presets_have_no_permissions():
    # местоимения / платформы / уведомления — самовыдача, без прав
    for key in ("pronouns", "platforms", "notifications"):
        for role in role_presets.get_preset(key)["roles"]:
            assert role_presets._mask(role["perms"]) == 0, key


def test_notification_roles_are_mentionable_but_pronouns_are_not():
    # уведомления пингуют => упоминаемые; местоимения — тихие
    assert all(r["mentionable"] for r in role_presets.get_preset("notifications")["roles"])
    assert not any(r["mentionable"] for r in role_presets.get_preset("pronouns")["roles"])


def test_media_preset_grants_only_light_perms():
    # оформление ≠ мод-власть: никаких kick/ban/manage_roles
    heavy = {"kick_members", "ban_members", "manage_roles", "manage_guild"}
    for role in role_presets.get_preset("media")["roles"]:
        assert not (set(role["perms"]) & heavy)


def test_get_preset_unknown_returns_none():
    assert role_presets.get_preset("does-not-exist") is None
