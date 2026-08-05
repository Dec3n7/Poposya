"""YtDlpAudioSource: сборка yt-dlp опций (cookies с гардом на файл, форс
player-клиентов). Без сети — проверяем только формирование словаря опций."""

from src.infrastructure.audio.ytdlp_source import YtDlpAudioSource


def test_cookiefile_applied_when_file_exists(tmp_path):
    f = tmp_path / "cookies.txt"
    f.write_text("# Netscape HTTP Cookie File\n")
    opts = YtDlpAudioSource(cookies_file=str(f))._opts_with_cookies({})
    assert opts["cookiefile"] == str(f)


def test_cookiefile_skipped_when_missing(tmp_path):
    # несуществующий путь не роняет extract — cookies просто не применяются
    src = YtDlpAudioSource(cookies_file=str(tmp_path / "nope.txt"))
    assert "cookiefile" not in src._opts_with_cookies({})


def test_cookies_from_browser_takes_precedence(tmp_path):
    f = tmp_path / "cookies.txt"
    f.write_text("x")
    opts = YtDlpAudioSource(
        cookies_from_browser="chrome", cookies_file=str(f)
    )._opts_with_cookies({})
    assert opts["cookiesfrombrowser"] == ("chrome",)
    assert "cookiefile" not in opts


def test_player_clients_parsed_to_extractor_args():
    opts = YtDlpAudioSource(player_clients="web_safari, web ,")._opts_with_cookies({})
    assert opts["extractor_args"]["youtube"]["player_client"] == ["web_safari", "web"]


def test_no_player_clients_no_extractor_args():
    assert "extractor_args" not in YtDlpAudioSource()._opts_with_cookies({})
