from dataclasses import dataclass

from src.application.tempvoice.use_cases import (
    ClaimTempChannelUseCase,
    CountTempChannelsUseCase,
    GetTempChannelUseCase,
    ListTempChannelsUseCase,
    RegisterTempChannelUseCase,
    ReleaseTempChannelUseCase,
)


@dataclass(frozen=True)
class TempVoiceContainer:
    register: RegisterTempChannelUseCase
    release: ReleaseTempChannelUseCase
    get: GetTempChannelUseCase
    claim: ClaimTempChannelUseCase
    count: CountTempChannelsUseCase
    list_channels: ListTempChannelsUseCase
