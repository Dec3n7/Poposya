from dataclasses import dataclass

from src.application.cinema.use_cases import (
    AddMovieUseCase,
    CancelNightUseCase,
    CloseNightPollUseCase,
    FinalizeRatingUseCase,
    GetCinemaProfileUseCase,
    GetMovieRatingsUseCase,
    GetMovieReviewsUseCase,
    GetMovieUseCase,
    ListPendingCinemaUseCase,
    ListWatchlistUseCase,
    OpenRatingUseCase,
    RateMovieUseCase,
    RegisterMovieMessageUseCase,
    RemoveMovieUseCase,
    ReviewMovieUseCase,
    StartMovieNightUseCase,
    TopWatchedUseCase,
    VoteMovieUseCase,
    VoteNightUseCase,
)


@dataclass(frozen=True)
class CinemaContainer:
    """Зависимости киноклуба; собирается в root_container."""

    add_movie: AddMovieUseCase
    register_message: RegisterMovieMessageUseCase
    vote_movie: VoteMovieUseCase
    list_watchlist: ListWatchlistUseCase
    top_watched: TopWatchedUseCase
    remove_movie: RemoveMovieUseCase
    start_night: StartMovieNightUseCase
    vote_night: VoteNightUseCase
    close_poll: CloseNightPollUseCase
    cancel_night: CancelNightUseCase
    open_rating: OpenRatingUseCase
    rate_movie: RateMovieUseCase
    review_movie: ReviewMovieUseCase
    finalize_rating: FinalizeRatingUseCase
    list_reviews: GetMovieReviewsUseCase
    list_ratings: GetMovieRatingsUseCase
    list_pending: ListPendingCinemaUseCase
    get_movie: GetMovieUseCase
    cinema_profile: GetCinemaProfileUseCase
