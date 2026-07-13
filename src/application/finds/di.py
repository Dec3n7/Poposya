from dataclasses import dataclass

from src.application.finds.use_cases import (
    ClaimFindUseCase,
    GetActiveFindUseCase,
    GetCollectionUseCase,
    GiftItemUseCase,
    ListLiveFindsUseCase,
    RegisterFindMessageUseCase,
    SpawnFindUseCase,
    SpecialWalkUseCase,
)


@dataclass(frozen=True)
class FindsContainer:
    """Зависимости «Ночных находок»; собирается в root_container."""

    spawn_find: SpawnFindUseCase
    register_find_message: RegisterFindMessageUseCase
    claim_find: ClaimFindUseCase
    gift_item: GiftItemUseCase
    get_collection: GetCollectionUseCase
    special_walk: SpecialWalkUseCase
    get_active_find: GetActiveFindUseCase
    list_live_finds: ListLiveFindsUseCase
