from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.db.models.base import Base


class GuildEntitlementModel(Base):
    """Тариф (подписка) сервера. Одна строка на гильдию; отсутствие строки =
    тариф по умолчанию (`ENTITLEMENTS_DEFAULT_TIER`). Выдаётся вручную оператором
    бота через веб-панель.

    expires_at = NULL — бессрочно; иначе по истечении сервер возвращается к
    тарифу по умолчанию (проверка идёт на чтении, фонового джоба не нужно)."""

    __tablename__ = "guild_entitlements"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tier: Mapped[str] = mapped_column(String(16), nullable=False)  # free | premium | pro
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    granted_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # id оператора
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class GuildTrialModel(Base):
    """Факт использования пробного периода сервером. ОТДЕЛЬНО от подписки: строка
    подписки удаляется при revoke, а запись о триале обязана пережить это —
    иначе связка «триал → revoke → снова триал» давала бы бесконечный Premium.
    Наличие строки = триал уже был; серверный enforcement, а не доверие UI."""

    __tablename__ = "guild_trials"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    granted_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # оператор
