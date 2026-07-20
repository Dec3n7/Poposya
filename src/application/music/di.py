from dataclasses import dataclass

from src.application.interfaces.audio_source import IAudioSource
from src.application.music.use_cases import (
    DeletePlaylistUseCase,
    ListLikedUseCase,
    ListPlaylistsUseCase,
    LoadPlaylistUseCase,
    RemoveLikedUseCase,
    ResolveLikedUseCase,
    SavePlaylistUseCase,
    ToggleLikeUseCase,
)
from src.application.player.use_cases import SavePlayerStateUseCase
from src.config import Settings
from src.domain.events.bus import IEventBus


@dataclass(frozen=True)
class MusicContainer:
    """Зависимости музыкальной фичи; собирается в root_container."""

    settings: Settings
    event_bus: IEventBus
    audio_source: IAudioSource
    save_playlist: SavePlaylistUseCase
    load_playlist: LoadPlaylistUseCase
    list_playlists: ListPlaylistsUseCase
    delete_playlist: DeletePlaylistUseCase
    toggle_like: ToggleLikeUseCase
    list_liked: ListLikedUseCase
    remove_liked: RemoveLikedUseCase
    resolve_liked: ResolveLikedUseCase
    # снапшот живого плеера для панели (бот пишет на каждое изменение)
    save_player_state: SavePlayerStateUseCase
